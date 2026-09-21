"""渲染任务：后台执行、串行渲染、实时日志、保存模式与「不保存」临时出图。

设计要点
--------
* **请求等待可见**：把 core 的日志接口（``info`` / ``warning`` / ``error`` / ``debug``）
  桥接到任务自己的日志队列，网页即可实时看到「请求：气候数据 cityId 237」
  「2.4s 后重试（2/4）…」「限速：等待 1.00s」「完成：HTTP 200，3721 字节，1.6s」。
* **渲染串行**：matplotlib 不是线程安全的，所有渲染都在全局 ``_RENDER_LOCK`` 内执行；
  任务本身在线程里排队，因此可以并发提交、串行出图。
* **一次渲染、两种去向**：同一张图既写文件（保存模式）又写内存缓冲（网页即时显示），
  ``core/chart.py::_save()`` 允许 targets 里混放 ``Path`` 与 ``BufferTarget``，
  因此不会为了预览而重复建图。
* **「不保存」开关**：``save_files=False`` 时只在内存产出图表与表格，``output/`` 不落任何文件
  （配置修改仍由配置服务正常保存，与本开关无关）。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from core import table_writer
from core.chart import (
    ChartError,
    render_city_chart,
    render_comparison_chart,
    render_multi_city_chart,
)
from core.city_index import CityIndex, CityLookupError, load_city_index
from core.config_loader import resolve_grid
from core.http_client import CityNotFoundError, FetchError, HttpClient
from core.models import CityClimate, city_display_name, coord_pair
from core.parser import NoClimateDataError, ParseError
from core.pipeline import (
    _apply_overwrite,          # 只读复用覆盖策略（skip / timestamp / overwrite）
    compare_basename,
    fetch_city,
    multi_basename,
    output_basename,
    paths_for,
    table_basename,
)

from . import rendering

#: 任务保留上限（超出后淘汰最旧的已结束任务）
MAX_TASKS = 40
#: 单个任务的日志上限（防止长时间任务把内存撑满）
MAX_LOGS = 6000

MODES = ("city", "compare", "multi")


class RenderError(RuntimeError):
    """渲染请求本身不合法（缺目标、模式未知等）。"""


class _Cancelled(Exception):
    """任务被用户取消。"""


# ---- 日志桥 --------------------------------------------------------------

class TaskLogger:
    """core 的日志接口 → 任务日志。

    不做级别过滤：网页端按需筛选，这样「DEBUG 的完成信息」「INFO 的请求与限速」
    都能被用户看到（CLI 默认只看 INFO，这里信息更全）。
    """

    def __init__(self, task: "Task") -> None:
        self.task = task

    def debug(self, msg: str) -> None:
        self.task.log("debug", str(msg))

    def info(self, msg: str) -> None:
        self.task.log("info", str(msg))

    def warning(self, msg: str) -> None:
        self.task.log("warning", str(msg))

    def error(self, msg: str) -> None:
        self.task.log("error", str(msg))


# ---- 产物 ----------------------------------------------------------------

@dataclass
class Artifact:
    """一份可下载/可预览的产物（在内存中，落盘与否由任务模式决定）。"""

    id: str
    kind: str                    # chart | table
    fmt: str
    name: str
    data: bytes
    path: Optional[str] = None
    city: str = ""
    label: str = ""

    def meta(self, task_id: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "fmt": self.fmt,
            "name": self.name,
            "size": len(self.data),
            "path": self.path,
            "city": self.city,
            "label": self.label,
            "saved": self.path is not None,
            "url": f"/api/render/{task_id}/artifact/{self.id}",
        }


# ---- 任务 ----------------------------------------------------------------

@dataclass
class Task:
    id: str
    mode: str
    profile: str
    save_files: bool
    cfg: dict[str, Any]
    request: dict[str, Any]
    created_at: float = dc_field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    status: str = "pending"          # pending | running | done | error | cancelled
    logs: list[dict[str, Any]] = dc_field(default_factory=list)
    artifacts: dict[str, Artifact] = dc_field(default_factory=dict)
    result: dict[str, Any] = dc_field(default_factory=dict)
    cancel_event: threading.Event = dc_field(default_factory=threading.Event)
    condition: threading.Condition = dc_field(default_factory=threading.Condition)
    _stamp: float = dc_field(default_factory=time.time)

    # ---- 日志 ----
    def log(self, level: str, message: str) -> None:
        with self.condition:
            if len(self.logs) >= MAX_LOGS:
                return
            self.logs.append({
                "i": len(self.logs),
                "t": round(time.time() - (self.started_at or self.created_at), 3),
                "level": level,
                "message": message,
            })
            self.condition.notify_all()

    def notify(self) -> None:
        with self.condition:
            self.condition.notify_all()

    def wait_for_logs(self, cursor: int, timeout: float = 20.0
                      ) -> tuple[list[dict[str, Any]], int, str]:
        """等待新日志（SSE 用）；返回 ``(新增日志, 新游标, 当前状态)``。"""
        with self.condition:
            if cursor >= len(self.logs) and self.status in ("pending", "running"):
                self.condition.wait(timeout)
            return self.logs[cursor:], len(self.logs), self.status

    # ---- 产物 ----
    def add_artifact(self, kind: str, fmt: str, name: str, data: bytes,
                     path: Optional[Path] = None, city: str = "",
                     label: str = "") -> Artifact:
        art_id = f"{kind}-{len(self.artifacts):03d}"
        artifact = Artifact(
            id=art_id, kind=kind, fmt=fmt, name=name, data=data,
            path=str(path) if path else None, city=city, label=label,
        )
        self.artifacts[art_id] = artifact
        return artifact

    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return round(end - (self.started_at or self.created_at), 2)

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise _Cancelled()

    def snapshot(self, cursor: int = 0) -> dict[str, Any]:
        with self.condition:
            logs = self.logs[cursor:]
            next_cursor = len(self.logs)
        return {
            "id": self.id,
            "mode": self.mode,
            "profile": self.profile,
            "save_files": self.save_files,
            "status": self.status,
            "elapsed": self.elapsed(),
            "logs": logs,
            "cursor": next_cursor,
            "artifacts": [a.meta(self.id) for a in self.artifacts.values()],
            "result": self.result,
        }


# ---- 任务注册表 ----------------------------------------------------------

_TASKS: dict[str, Task] = {}
_TASKS_LOCK = threading.Lock()
_RENDER_LOCK = threading.Lock()      # matplotlib 串行渲染


def get(task_id: str) -> Optional[Task]:
    with _TASKS_LOCK:
        return _TASKS.get(task_id)


def list_tasks() -> list[Task]:
    with _TASKS_LOCK:
        return sorted(_TASKS.values(), key=lambda t: t._stamp, reverse=True)


def _register(task: Task) -> None:
    with _TASKS_LOCK:
        _TASKS[task.id] = task
        if len(_TASKS) > MAX_TASKS:
            finished = sorted(
                (t for t in _TASKS.values() if t.status in ("done", "error", "cancelled")),
                key=lambda t: t._stamp,
            )
            for stale in finished[: len(_TASKS) - MAX_TASKS]:
                _TASKS.pop(stale.id, None)


def cancel(task_id: str) -> bool:
    task = get(task_id)
    if task is None or task.status not in ("pending", "running"):
        return False
    task.cancel_event.set()
    task.log("warning", "已请求取消，等待当前步骤结束…")
    return True


# ---- 提交与执行 ----------------------------------------------------------

def submit(request: dict[str, Any], cfg: dict[str, Any]) -> Task:
    """提交一个渲染任务（cfg 应是已经过校验与规范化的最终配置）。"""
    mode = str(request.get("mode") or "city").lower()
    if mode not in MODES:
        raise RenderError(f"未知的出图模式：{mode}")
    targets = [str(item) for item in (request.get("cities") or []) if str(item).strip()]
    if not targets:
        raise RenderError("请至少提供一个城市（cityId 或城市名）")
    if mode == "compare" and len(targets) < 2:
        raise RenderError("多城市对比至少需要 2 个城市")
    if mode == "multi" and len(targets) < 2:
        raise RenderError("多图至少需要 2 个城市")

    task = Task(
        id=uuid.uuid4().hex[:12],
        mode=mode,
        profile=str(cfg.get("profile_name") or ""),
        save_files=bool(request.get("save_files", True)),
        cfg=cfg,
        request={**request, "cities": targets},
    )
    _register(task)
    thread = threading.Thread(target=_run, args=(task,), name=f"render-{task.id}", daemon=True)
    thread.start()
    return task


def _run(task: Task) -> None:
    task.status = "running"
    task.started_at = time.time()
    task.log("info", f"任务开始（{_mode_label(task.mode)}）")
    try:
        with _RENDER_LOCK:
            task.check_cancel()
            _execute(task)
        task.status = "done"
        task.log("info", "任务完成")
    except _Cancelled:
        task.status = "cancelled"
        task.log("warning", "任务已取消（已完成的部分仍然可用）")
    except Exception as exc:  # noqa: BLE001 - 兜底，避免线程静默退出
        task.status = "error"
        task.log("error", f"任务失败：{type(exc).__name__}: {exc}")
    finally:
        task.finished_at = time.time()
        task.result["elapsed"] = task.elapsed()
        task.result["status"] = task.status
        task.notify()


def _mode_label(mode: str) -> str:
    return {"city": "单城 / 批量统计图", "compare": "多城市对比图", "multi": "多图（同画布）"}[mode]


def _execute(task: Task) -> None:
    cfg = task.cfg
    logger = TaskLogger(task)
    dirs = paths_for(cfg)
    out_dir: Path = dirs["out_dir"]
    policy = str(cfg["output"].get("overwrite", "overwrite"))

    task.log("info", f"配置：{task.profile}（{cfg.get('profile_description') or '无说明'}）")
    task.log("info", "输出方式：" + ("保存到本地（output 目录）并即时显示"
                                     if task.save_files else "仅网页显示，不写入本地（临时出图）"))
    task.log("info", f"缓存目录：{dirs['cache_dir']}")

    client = HttpClient(cfg["fetch"], logger=logger,
                        cache_dir=dirs["cache_dir"], raw_dir=dirs["raw_dir"])
    if task.save_files:
        out_dir.mkdir(parents=True, exist_ok=True)

    city_ids, index = _resolve_targets(task, client, cfg, dirs, logger)
    if not city_ids:
        raise RenderError("没有解析出任何城市编号")

    try:
        if task.mode == "city":
            _run_city_mode(task, client, cfg, out_dir, policy, city_ids, index, logger)
        elif task.mode == "compare":
            _run_compare_mode(task, client, cfg, out_dir, policy, city_ids, index, logger)
        else:
            _run_multi_mode(task, client, cfg, out_dir, policy, city_ids, index, logger)
    finally:
        task.result["requests"] = {"count": client.request_count,
                                   "retries": client.retry_count}
        task.result["output_dir"] = None if not task.save_files else str(out_dir)
        task.result["mode"] = task.mode
        task.result["profile"] = task.profile
        task.result["saved"] = task.save_files
        task.log("info", f"请求统计：共 {client.request_count} 次请求，其中重试 {client.retry_count} 次")


# ---- 城市解析 ------------------------------------------------------------

def _looks_like_id(text: str) -> bool:
    return text.strip().isdigit()


def _resolve_targets(task: Task, client: HttpClient, cfg: dict[str, Any], dirs: dict[str, Path],
                     logger) -> tuple[list[int], Optional[CityIndex]]:
    raw: list[str] = list(task.request.get("cities") or [])
    ids: list[int] = []
    names: list[str] = []
    for item in raw:
        if _looks_like_id(item):
            ids.append(int(item))
        else:
            names.append(item.strip())

    index: Optional[CityIndex] = None
    if names:
        task.log("info", f"开始按名称反查 cityId，共 {len(names)} 个：{'、'.join(names)}")
        try:
            index = load_city_index(
                client, cfg, dirs["cache_dir"] / "city_index.json", logger,
                refresh=bool(task.request.get("refresh_index")),
            )
        except (CityLookupError, FetchError) as exc:
            raise RenderError(f"城市索引获取失败：{exc}") from exc
        for name in names:
            try:
                entry = index.resolve_one(name)
            except CityLookupError as exc:
                task.log("error", f"「{name}」未能反查到城市编号：{exc}")
                continue
            task.log("info", f"「{name}」→ cityId {entry.city_id}（{entry.city_name}，{entry.mem_name}）")
            ids.append(entry.city_id)

    unique: list[int] = []
    seen: set[int] = set()
    for city_id in ids:
        if city_id not in seen:
            seen.add(city_id)
            unique.append(city_id)
    if len(unique) != len(ids):
        task.log("info", f"已去重 {len(ids) - len(unique)} 个重复城市")
    return unique, index


def _city_label(city_id: int, index: Optional[CityIndex], cfg: dict[str, Any]) -> str:
    if index is None:
        return f"cityId {city_id}"
    entry = index.by_id(city_id)
    if entry is None:
        return f"cityId {city_id}"
    coords = coord_pair(entry.latitude, entry.longitude, cfg)
    return f"{city_display_name(entry, cfg)}（{city_id}，{entry.mem_name} {coords}）"


# ---- 渲染辅助 ------------------------------------------------------------

def _unique_formats(formats: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in formats:
        fmt = rendering.normalize_chart_format(raw)
        if fmt and fmt not in seen:
            seen.add(fmt)
            result.append(fmt)
    return result


def _split_saved(saved: list[Any], buffers: list[Any]) -> tuple[list[Path], list[Any]]:
    buffer_ids = {id(item) for item in buffers}
    files = [item for item in saved if id(item) not in buffer_ids]
    memories = [item for item in saved if id(item) in buffer_ids]
    return files, memories


def _series_labels(report: dict[str, Any]) -> list[str]:
    """取「本次实际绘出的要素」名。

    core 的 report 两种形状都有：单城/多图是 ``[{"label": ...}, ...]``，
    对比图是 ``["日均气温", ...]``（字符串），这里统一成字符串列表。
    """
    labels: list[str] = []
    for item in report.get("series") or []:
        if isinstance(item, dict):
            labels.append(str(item.get("label", "")))
        else:
            labels.append(str(item))
    return [label for label in labels if label]


def _render_charts(render_fn: Callable[[list[Any], dict], list[Any]], *,
                   task: Task, cfg: dict[str, Any], out_dir: Path, policy: str,
                   base_name: str, label: str, city: str = "") -> dict[str, Any]:
    """一次渲染，同时产出落盘文件与内存预览。

    网页显示区**始终**渲染 PNG 预览（``rendering.PREVIEW_FORMAT``）；
    ``output.chart_formats`` 只决定**落盘**格式——两者互不干扰。
    """
    formats = _unique_formats(cfg["output"].get("chart_formats") or [])
    preview = _unique_formats([rendering.PREVIEW_FORMAT, *formats])

    file_targets: list[Path] = []
    if task.save_files and formats:
        candidates = [out_dir / f"{base_name}.{fmt}" for fmt in formats]
        file_targets = _apply_overwrite(candidates, policy)
        skipped = [p.name for p in candidates if p not in file_targets]
        if skipped:
            task.log("info", f"按覆盖策略跳过已存在的文件：{'、'.join(skipped)}")
    elif not formats:
        task.log("info", "配置未启用任何图片格式：不落盘，仅网页显示预览图")

    buffers = [rendering.BufferTarget(fmt) for fmt in preview]
    report: dict[str, Any] = {}
    saved = render_fn(file_targets + buffers, report)
    files, memories = _split_saved(saved, buffers)

    for path in files:
        task.log("info", f"已保存图片：{path}")

    # 预览缓冲与落盘文件是同一张图的两种去向：把落盘路径回写到同格式的预览产物上，
    # 这样界面上「已保存」与「另存本地图片」指向的就是真实的 output 文件。
    written = {str(path.suffix).lstrip(".").lower(): path for path in files}
    artifacts: list[dict[str, Any]] = []
    for buffer in memories:
        fmt = str(buffer.suffix).lstrip(".")
        artifact = task.add_artifact("chart", fmt, f"{base_name}.{fmt}",
                                     buffer.to_bytes(), written.get(fmt.lower()),
                                     city=city, label=label)
        artifacts.append(artifact.meta(task.id))
    series = _series_labels(report)
    if series:
        task.log("info", f"绘出要素：{'、'.join(series)}")
    return {
        "series": series,
        "charts": artifacts,
        "files": [str(p) for p in files],
    }


def _write_tables(task: Task, cfg: dict[str, Any], city: CityClimate, out_dir: Path,
                  policy: str) -> dict[str, Any]:
    """生成表格：内容在内存产出（预览用），保存模式下再写盘。"""
    formats = [str(f).lower() for f in (cfg["output"].get("table_formats") or [])]
    if not formats:
        return {"tables": [], "files": []}
    base = table_basename(cfg, city)
    artifacts: list[dict[str, Any]] = []
    files: list[str] = []
    for fmt, data in rendering.table_bytes(city, cfg, formats):
        entry = table_writer.WRITERS.get(fmt)
        if entry is None:
            continue
        name = f"{base}{entry[1]}"
        path: Optional[Path] = None
        if task.save_files:
            writable = _apply_overwrite([out_dir / name], policy)
            if writable:
                path = writable[0]
                try:
                    path.write_bytes(data)
                    files.append(str(path))
                    task.log("info", f"已保存表格：{path}")
                except OSError as exc:
                    task.log("error", f"表格写出失败：{path}（{exc}）")
                    path = None
            else:
                task.log("info", f"按覆盖策略跳过已存在的表格：{name}")
        artifact = task.add_artifact("table", fmt, name, data, path,
                                     city=city.city_name, label=fmt.upper())
        artifacts.append(artifact.meta(task.id))
    return {"tables": artifacts, "files": files}


def _fetch(task: Task, client: HttpClient, cfg: dict[str, Any], city_id: int, logger
           ) -> tuple[Optional[CityClimate], Optional[str]]:
    try:
        city = fetch_city(client, cfg, city_id, logger)
        return city, None
    except (CityNotFoundError, NoClimateDataError, ParseError, FetchError) as exc:
        return None, str(exc)


# ---- 三种模式 ------------------------------------------------------------

def _run_city_mode(task: Task, client: HttpClient, cfg: dict[str, Any], out_dir: Path,
                   policy: str, city_ids: list[int], index: Optional[CityIndex], logger) -> None:
    results: list[dict[str, Any]] = []
    all_artifacts: list[dict[str, Any]] = []
    total = len(city_ids)
    for position, city_id in enumerate(city_ids, start=1):
        task.check_cancel()
        label = _city_label(city_id, index, cfg)
        task.log("info", f"[{position}/{total}] 开始处理 {label}")
        city, error = _fetch(task, client, cfg, city_id, logger)
        if city is None:
            task.log("error", f"[{position}/{total}] {label} 取数失败：{error}")
            results.append({"city_id": city_id, "name": label, "ok": False, "error": error})
            continue

        chart_base = output_basename(cfg, city)
        try:
            tables = _write_tables(task, cfg, city, out_dir, policy)
        except Exception as exc:  # noqa: BLE001 - 表格失败不应阻断出图
            task.log("error", f"表格生成失败：{exc}")
            tables = {"tables": [], "files": []}

        charts: dict[str, Any] = {"series": [], "charts": [], "files": []}
        try:
            charts = _render_charts(
                lambda targets, report: render_city_chart(city, cfg, targets, logger, report),
                task=task, cfg=cfg, out_dir=out_dir, policy=policy,
                base_name=chart_base, label=city.city_name, city=city.city_name,
            )
        except ChartError as exc:
            task.log("warning", f"绘图跳过：{exc}")
        except Exception as exc:  # noqa: BLE001
            task.log("error", f"绘图失败：{exc}")

        all_artifacts.extend(tables["tables"] + charts["charts"])
        results.append({
            "city_id": city_id,
            "name": city.city_name,
            "ok": True,
            "series": charts["series"],
            "tables": tables["tables"],
            "charts": charts["charts"],
            "files": tables["files"] + charts["files"],
            "period": city.period_note(),
        })
        task.log("info", f"[{position}/{total}] {city.city_name}（{city_id}）完成："
                         f"{len(tables['tables'])} 个表格，{len(charts['charts'])} 张图")

    task.result["cities"] = results
    task.result["artifacts"] = all_artifacts
    ok_count = sum(1 for item in results if item["ok"])
    task.result["summary"] = f"成功 {ok_count} 个，失败 {total - ok_count} 个"
    task.log("info", task.result["summary"])


def _fetch_many(task: Task, client: HttpClient, cfg: dict[str, Any], city_ids: list[int],
                index: Optional[CityIndex], logger, mode_label: str
                ) -> tuple[list[CityClimate], list[dict[str, Any]]]:
    cities: list[CityClimate] = []
    results: list[dict[str, Any]] = []
    total = len(city_ids)
    for position, city_id in enumerate(city_ids, start=1):
        task.check_cancel()
        label = _city_label(city_id, index, cfg)
        task.log("info", f"[{position}/{total}] {mode_label}：取数 {label}")
        city, error = _fetch(task, client, cfg, city_id, logger)
        if city is None:
            task.log("error", f"[{position}/{total}] {label} 取数失败：{error}")
            results.append({"city_id": city_id, "name": label, "ok": False, "error": error})
            continue
        cities.append(city)
        results.append({"city_id": city_id, "name": city.city_name, "ok": True})
    return cities, results


def _run_compare_mode(task: Task, client: HttpClient, cfg: dict[str, Any], out_dir: Path,
                      policy: str, city_ids: list[int], index: Optional[CityIndex],
                      logger) -> None:
    cities, results = _fetch_many(task, client, cfg, city_ids, index, logger, "对比")
    task.result["cities"] = results
    if not cities:
        task.result["artifacts"] = []
        task.result["summary"] = "没有可用于对比的城市"
        task.log("error", task.result["summary"])
        return

    metric = str((cfg.get("compare") or {}).get("metric", "meanTemp"))
    task.log("info", f"用于对比的城市：{len(cities)} 个，对比元素：{metric}")
    base = compare_basename(cfg, cities)
    try:
        charts = _render_charts(
            lambda targets, report: render_comparison_chart(cities, cfg, targets, logger, report),
            task=task, cfg=cfg, out_dir=out_dir, policy=policy,
            base_name=base, label=f"{len(cities)} 城对比", city="、".join(c.city_name for c in cities),
        )
    except ChartError as exc:
        charts = {"series": [], "charts": [], "files": []}
        task.log("error", f"对比图生成失败：{exc}")
    task.result["artifacts"] = charts["charts"]
    task.result["compare"] = {"series": charts["series"], "files": charts["files"]}
    task.result["summary"] = f"取数成功 {len(cities)} 个，失败 {len(results) - len(cities)} 个"


def _run_multi_mode(task: Task, client: HttpClient, cfg: dict[str, Any], out_dir: Path,
                    policy: str, city_ids: list[int], index: Optional[CityIndex],
                    logger) -> None:
    cities, results = _fetch_many(task, client, cfg, city_ids, index, logger, "多图")
    task.result["cities"] = results
    if not cities:
        task.result["artifacts"] = []
        task.result["summary"] = "没有可用于多图的城市"
        task.log("error", task.result["summary"])
        return

    cols, rows = resolve_grid(cfg.get("multi") or {}, len(cities))
    capacity = cols * rows
    drawn = cities[:capacity]
    grid = f"{cols}x{rows}"
    skipped = [city.city_name for city in cities[capacity:]]
    task.log("info", f"画布 {grid}（{capacity} 格），实际绘制 {len(drawn)} 个城市")
    if len(cities) < capacity:
        task.log("warning",
                 f"城市数量 {len(cities)} 与画布 {grid}（{capacity} 格）不匹配："
                 f"按行优先只画前 {len(cities)} 格，剩余 {capacity - len(cities)} 格留空")
    elif skipped:
        task.log("warning",
                 f"城市数量 {len(cities)} 超过画布 {grid}（{capacity} 格）："
                 f"只画前 {capacity} 个，未绘制：{'、'.join(skipped)}")

    base = multi_basename(cfg, drawn, grid)
    try:
        charts = _render_charts(
            lambda targets, report: render_multi_city_chart(
                drawn, cfg, targets, logger, report, grid=(cols, rows)),
            task=task, cfg=cfg, out_dir=out_dir, policy=policy,
            base_name=base, label=f"{len(drawn)} 城多图 {grid}",
            city="、".join(c.city_name for c in drawn),
        )
    except ChartError as exc:
        charts = {"series": [], "charts": [], "files": []}
        task.log("error", f"多图生成失败：{exc}")
    task.result["artifacts"] = charts["charts"]
    task.result["multi"] = {
        "grid": grid,
        "series": charts["series"],
        "files": charts["files"],
        "drawn": [{"city_id": c.city_id, "name": c.city_name} for c in drawn],
        "skipped": skipped,
    }
    task.result["summary"] = (f"取数成功 {len(cities)} 个，失败 {len(results) - len(cities)} 个，"
                              f"画布 {grid}，已绘制 {len(drawn)} 格")


# ---- 给路由用的辅助 ------------------------------------------------------

def apply_request_overrides(cfg: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """把「本次出图」的开关写进配置副本（不改动已保存的配置文件）。"""
    cfg = dict(cfg)
    cfg["fetch"] = dict(cfg["fetch"])
    cfg["fetch"]["cache"] = dict(cfg["fetch"].get("cache") or {})
    if request.get("refresh_cache") or request.get("refresh_index"):
        cfg["fetch"]["cache"]["enabled"] = False
    if request.get("compare_metric"):
        cfg["compare"] = dict(cfg.get("compare") or {})
        cfg["compare"]["metric"] = request["compare_metric"]
    if request.get("grid"):
        cfg["multi"] = dict(cfg.get("multi") or {})
        cfg["multi"]["grid"] = request["grid"]
    return cfg
