"""渲染适配层：在 ``core`` 之上提供「内存产物」能力。

设计原则
--------
``core/`` 是从参考 CLI 项目**原样复制**的只读副本，本项目不做任何修改。
因此本模块只做适配，不重写任何绘图/表格逻辑：

* :class:`BufferTarget` —— 带 ``suffix`` 的内存文件对象。``core/chart.py`` 的
  ``_save()`` 只依赖 ``path.suffix`` 与 ``fig.savefig(path, ...)``，
  而 matplotlib 的 ``savefig`` 原生支持文件类对象，于是「不落盘出图」无需改动 core。
* :func:`city_chart_bytes` / :func:`comparison_chart_bytes` / :func:`multi_chart_bytes`
  —— 把绘图结果渲染进内存，返回 ``[(格式, 字节)]``。
* :func:`table_bytes` —— 复用 ``core/table_writer`` 的**真实写出器**（写临时文件后读回，
  随即删除），保证与 CLI 产物字节级一致，避免任何口径漂移。

「网页即时显示」始终额外出图一张 PNG 预览（见 :data:`PREVIEW_FORMAT`），
与用户配置的输出格式相互独立。
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any, Optional, Sequence

from core import table_writer
from core.chart import render_city_chart, render_comparison_chart, render_multi_city_chart
from core.models import CityClimate

#: 网页显示区固定使用的预览格式（不受 ``output.chart_formats`` 影响）
PREVIEW_FORMAT = "png"

#: matplotlib / 图片格式的规范化别名
_FORMAT_ALIASES = {"jpeg": "jpg", "markdown": "md"}


class BufferTarget(io.BytesIO):
    """带 ``suffix`` 的内存文件对象，可直接交给 matplotlib 的 ``savefig``。

    ``core/chart.py`` 的 ``_save()`` 会读取 ``path.suffix`` 推断格式、
    并把结果 ``append`` 回列表，因此这里额外暴露 ``name`` 便于日志排查。
    """

    def __init__(self, suffix: str) -> None:
        super().__init__()
        text = str(suffix or "").lstrip(".").lower()
        self.suffix = f".{text}" if text else ".png"
        self.name = f"memory{self.suffix}"

    def to_bytes(self) -> bytes:
        """取出已写入的字节（不关闭缓冲区，便于重复取用）。"""
        self.seek(0)
        return self.read()


def normalize_chart_format(fmt: Any) -> str:
    """把配置里的图片格式规范化为 matplotlib 可识别的名字。"""
    text = str(fmt or "").strip().lstrip(".").lower()
    return _FORMAT_ALIASES.get(text, text)


def normalize_table_format(fmt: Any) -> str:
    """把配置里的表格格式规范化为 ``table_writer.WRITERS`` 的键。"""
    text = str(fmt or "").strip().lstrip(".").lower()
    return _FORMAT_ALIASES.get(text, text)


def _unique_formats(formats: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in formats:
        fmt = normalize_chart_format(raw)
        if fmt and fmt not in seen:
            seen.add(fmt)
            result.append(fmt)
    return result


def _render(render_fn, targets: list[BufferTarget]) -> list[tuple[str, bytes]]:
    render_fn(targets)
    return [(target.suffix.lstrip("."), target.to_bytes()) for target in targets]


# ---- 绘图 → 内存 --------------------------------------------------------

def city_chart_bytes(city: CityClimate, cfg: dict[str, Any], formats: Sequence[Any],
                     logger=None, report: Optional[dict[str, Any]] = None
                     ) -> list[tuple[str, bytes]]:
    """单城统计图渲染到内存，返回 ``[(格式, 字节)]``。"""
    fmts = _unique_formats(formats)
    if not fmts:
        return []
    targets = [BufferTarget(fmt) for fmt in fmts]
    return _render(lambda ts: render_city_chart(city, cfg, ts, logger, report), targets)


def comparison_chart_bytes(cities: Sequence[CityClimate], cfg: dict[str, Any],
                           formats: Sequence[Any], logger=None,
                           report: Optional[dict[str, Any]] = None
                           ) -> list[tuple[str, bytes]]:
    """多城市对比图渲染到内存。"""
    fmts = _unique_formats(formats)
    if not fmts:
        return []
    targets = [BufferTarget(fmt) for fmt in fmts]
    return _render(
        lambda ts: render_comparison_chart(list(cities), cfg, ts, logger, report),
        targets,
    )


def multi_chart_bytes(cities: Sequence[CityClimate], cfg: dict[str, Any],
                      formats: Sequence[Any], logger=None,
                      report: Optional[dict[str, Any]] = None,
                      grid: Optional[tuple[int, int]] = None
                      ) -> list[tuple[str, bytes]]:
    """多图（多城市同画布）渲染到内存。"""
    fmts = _unique_formats(formats)
    if not fmts:
        return []
    targets = [BufferTarget(fmt) for fmt in fmts]
    return _render(
        lambda ts: render_multi_city_chart(list(cities), cfg, ts, logger, report, grid=grid),
        targets,
    )


# ---- 表格 → 内存 --------------------------------------------------------

def table_bytes(city: CityClimate, cfg: dict[str, Any], formats: Sequence[Any]
                ) -> list[tuple[str, bytes]]:
    """按配置生成表格内容（不落盘），返回 ``[(格式, 字节)]``。

    实现方式：把 ``core/table_writer`` 的真实写出器指向临时文件，读回字节后立即清理。
    这样做虽然多一次磁盘往返，但**完全复用了 CLI 的表格逻辑**，不会出现口径漂移。
    """
    wanted: list[tuple[str, Any, str]] = []
    for raw in formats:
        fmt = normalize_table_format(raw)
        entry = table_writer.WRITERS.get(fmt)
        if entry is None:
            continue
        wanted.append((fmt, entry[0], entry[1]))
    if not wanted:
        return []

    results: list[tuple[str, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="wmo_table_") as tmp:
        for fmt, writer, ext in wanted:
            path = Path(tmp) / f"table{ext}"
            writer(path, city, cfg)
            results.append((fmt, path.read_bytes()))
    return results


def table_text_encoding(fmt: str) -> str:
    """表格内容在网页预览时使用的解码方式（CSV 带 BOM）。"""
    return "utf-8-sig" if normalize_table_format(fmt) == "csv" else "utf-8"


def is_text_table(fmt: str) -> bool:
    """该表格格式能否直接在网页上以文本/表格形式预览。"""
    return normalize_table_format(fmt) in ("csv", "md", "json")
