"""配置服务：读取 / 校验 / 保存修改 / 另存（全量 · 继承差量）。

与参考项目的关系
----------------
本模块**只读取** ``core`` 的配置能力，不修改它：

* 配置来源与优先级完全沿用 ``core/config_loader``（内置 ``config/profiles.yaml``
  → ``config/custom.json`` / ``config/custom/*.json`` → ``config/custom.yaml``
  → ``config/custom/*.yaml``），排序直接复用其 ``_custom_config_sources()``，
  避免两处顺序逻辑漂移。
* 「另存 · 继承差量」的**基线**通过 ``resolve_profile()`` 解析一个只含
  ``extends`` / ``apply_styles`` 的骨架得到，因此继承链与命名样式都由 core 自己算，
  本模块不重复实现合并规则。

写盘规则（内置配置只读）
------------------------
网页的一切保存/另存都写入 ``config/custom/<名称>.yaml``，采用与
``config/custom/简2.yaml`` 一致的**单配置裸文件**结构（配置名取文件名）：

```yaml
description: 说明文案
extends: 简图            # 仅「继承差量」模式
apply_styles: [样式名]    # 原配置有则保留
figure: {...}            # 仅差异项（「保存全量配置」时则是完整配置）
```

``config/profiles.yaml`` 永不被写入；保存到与内置配置同名的文件时，
自定义文件按 core 的加载顺序**覆盖**内置同名配置（内置文件本身不变）。
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from core.config_loader import (
    _custom_config_sources,          # 只读复用其排序，保证与 core 加载顺序一致
    CUSTOM_PROFILES_DIR,
    DEFAULT_PROFILES_PATH,
    ConfigError,
    deep_merge,
    dump_config,
    load_config,
    load_profiles_file,
    resolve_profile,
    set_by_path,
    validate_config,
)
from core.models import normalize_series_key

from . import schema

#: 网页保存的配置目录（一配置一文件）
SAVE_DIR = CUSTOM_PROFILES_DIR

_YAML_OPTIONS = dict(allow_unicode=True, sort_keys=False,
                     default_flow_style=None, width=110)

#: 配置名会被用作文件名（配置名 = 文件主干），因此必须与文件系统兼容：
#: 只禁止会破坏路径或 Windows 文件名的字符；配置名本身**不做任何静默替换**。
_NAME_RE = re.compile(r'^[^\\/:*?"<>|\r\n\t]{1,60}$')

_RESERVED_WIN = (
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

_FILE_HEADER = (
    "# 本文件由 DrawClimateChartWeb 保存/另存生成。\n"
    "# 单配置裸文件：配置名取文件名；可 extends 其他配置，也可 apply_styles 复用命名样式。\n"
    "# 完整配置项见 config/README.md。\n"
)


class ConfigServiceError(RuntimeError):
    """配置操作失败（名称非法、写盘失败等）。"""


# ---- 配置清单 ------------------------------------------------------------

@dataclass(frozen=True)
class ProfileSource:
    """一套配置的来源信息。"""

    name: str
    description: str
    source: str          # 相对项目根的路径，或 "内置 profiles.yaml"
    builtin: bool        # 是否定义于内置 profiles.yaml
    shadowed: bool       # 是否被自定义配置同名覆盖
    shape: str           # builtin | wrapped | single
    editable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "builtin": self.builtin,
            "shadowed": self.shadowed,
            "shape": self.shape,
        }


def _read_yaml_file(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        raise ConfigServiceError(f"配置不是合法 YAML：{path}（{exc}）") from exc
    except OSError as exc:
        raise ConfigServiceError(f"配置读取失败：{path}（{exc}）") from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ConfigServiceError(f"配置顶层必须是对象：{path}")
    return payload


def _profiles_in_file(path: Path) -> tuple[dict[str, Any], str]:
    """返回 ``(profiles, shape)``；结构判定与 ``core/config_loader`` 完全一致。"""
    payload = _read_yaml_file(path)
    if isinstance(payload.get("profiles"), dict):
        return {str(k): v for k, v in payload["profiles"].items()}, "wrapped"
    body = dict(payload)
    body.pop("name", None)
    body.pop("profile_name", None)
    body.pop("profile_description", None)
    body.pop("styles", None)
    return {path.stem: body}, "single"


def _rel(path: Optional[Path]) -> str:
    if path is None:
        return "内置 profiles.yaml"
    try:
        return path.relative_to(Path(__file__).resolve().parent.parent).as_posix()
    except ValueError:
        return str(path)


def scan_profiles() -> list[ProfileSource]:
    """扫描全部配置来源，返回去重后的配置清单（同名时后者胜出）。

    优先级与 core 的加载顺序完全一致：内置 → custom.json → custom/*.json
    → custom.yaml → custom/*.yaml。``shadowed=True`` 表示该名称在内置里也有定义，
    但当前生效的是自定义文件里的版本。
    """
    builtin_profiles: dict[str, Any] = {}
    if DEFAULT_PROFILES_PATH.is_file():
        builtin_profiles, _ = _profiles_in_file(DEFAULT_PROFILES_PATH)

    entries: dict[str, ProfileSource] = {}
    for name, body in builtin_profiles.items():
        desc = body.get("description", "") if isinstance(body, dict) else ""
        entries[name] = ProfileSource(name, str(desc), _rel(None), True, False, "builtin")

    for path in _custom_config_sources():
        try:
            profiles, shape = _profiles_in_file(path)
        except ConfigServiceError:
            continue
        for name, body in profiles.items():
            desc = body.get("description", "") if isinstance(body, dict) else ""
            entries[name] = ProfileSource(
                name, str(desc), _rel(path),
                builtin=name in builtin_profiles,   # 名称与内置同名（可能覆盖内置）
                shadowed=name in builtin_profiles,
                shape=shape,
            )

    # 与 core.list_profiles 的排序口径一致：default 恒在最前，其余按名称
    result = list(entries.values())
    result.sort(key=lambda item: (item.name != "default", item.name))
    return result


def default_profile_name() -> str:
    """内置 ``profiles.yaml`` 声明的默认配置名。"""
    try:
        doc = _read_yaml_file(DEFAULT_PROFILES_PATH)
    except ConfigServiceError:
        return "default"
    return str(doc.get("default_profile") or "default")


def profile_names() -> list[str]:
    return [item.name for item in scan_profiles()]


def pick_profile(last: str, names: Optional[list[str]] = None,
                 default: Optional[str] = None) -> str:
    """决定本次应选用的配置：**上次使用过的**（且仍然存在）优先，否则默认配置。

    纯函数（``names`` / ``default`` 可注入），便于复用与测试。
    规则：
    1. ``last`` 非空且仍在清单里 → 用它；
    2. 否则默认配置在清单里 → 用默认配置；
    3. 再否则取清单第一项；清单为空时兜底 ``"default"``。
    """
    catalog = list(names) if names is not None else profile_names()
    fallback = default or default_profile_name()
    wanted = str(last or "").strip()
    if wanted and wanted in catalog:
        return wanted
    if fallback and fallback in catalog:
        return fallback
    return catalog[0] if catalog else "default"


def _raw_definition(name: str) -> dict[str, Any]:
    """取某配置在其来源文件里的**原始定义**（用于保留 extends / apply_styles）。"""
    for path in reversed(_custom_config_sources()):
        try:
            profiles, _ = _profiles_in_file(path)
        except ConfigServiceError:
            continue
        if name in profiles and isinstance(profiles[name], dict):
            return copy.deepcopy(profiles[name])
    if DEFAULT_PROFILES_PATH.is_file():
        profiles, _ = _profiles_in_file(DEFAULT_PROFILES_PATH)
        body = profiles.get(name)
        if isinstance(body, dict):
            return copy.deepcopy(body)
    return {}


# ---- 解析与校验 ----------------------------------------------------------

def resolve(profile: Optional[str] = None,
            values: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """解析出最终配置，并按点路径套用表单值（值应已通过 :func:`normalize_values`）。

    配置名不存在时回退到内置默认（例如「另存为新配置」时新名称尚未落盘）。
    """
    try:
        cfg = load_config(profile)
    except ConfigError:
        cfg = load_config(None)
    for path, value in (values or {}).items():
        field = schema.field_index().get(path)
        if field is not None and field.readonly:
            continue
        set_by_path(cfg, path, copy.deepcopy(value))
    return cfg


def normalize_values(values: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """按 Schema 逐项规范化；返回 ``(规范化结果, 错误列表)``。"""
    index = schema.field_index()
    normalized: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    for path, raw in (values or {}).items():
        field = index.get(path)
        if field is None:
            errors.append({"path": path, "message": "未知配置项，请检查拼写"})
            continue
        if field.readonly:
            continue
        try:
            normalized[path] = schema.normalize_value(field, raw)
        except schema.FieldError as exc:
            errors.append({"path": path, "message": exc.message})
    return normalized, errors


#: 会被 ``core/chart.py::_context`` / ``_multi_context`` 用于格式化的文本模板
_TEXT_TEMPLATE_PATHS = (
    "figure.title.text", "figure.subtitle.text", "figure.credit.text",
    "figure.mean_rain_line.annotate_template", "table.title_template",
    "table.note_template",
)


def _cross_field_notes(cfg: dict[str, Any]) -> list[str]:
    """Schema 与 ``validate_config`` 之外的补充告警（不阻断出图）。"""
    notes: list[str] = []
    for path in _TEXT_TEMPLATE_PATHS:
        if path.startswith("table."):
            continue          # 表格模板走 table_writer，上下文与绘图不同
        text = _deep_get(cfg, path)
        if not isinstance(text, str):
            continue
        used = [name for name in schema.COMPARE_ONLY_PLACEHOLDERS if f"{{{name}}}" in text]
        if used:
            notes.append(
                f"{path} 使用了 {'、'.join('{' + n + '}' for n in used)}，"
                f"该占位符仅在「多城市对比」与「多图」模式下可用；单城出图会因缺少占位符而失败"
            )
    for key, scfg in (cfg.get("series") or {}).items():
        if not isinstance(scfg, dict) or scfg.get("chart_type") != "fill_between":
            continue
        raw = str(scfg.get("fill_between_key", "") or "")
        try:
            normalize_series_key(raw)
        except KeyError:
            notes.append(
                f"series.{key}.chart_type 为 fill_between，但 fill_between_key="
                f"{raw!r} 不是可识别的元素键，绘图时会报错；"
                f"请改为 {'/'.join(schema.SERIES_KEYS)} 之一"
            )
    return notes


def _compare_color_map_notes(cfg: dict[str, Any],
                             compare_cities: Optional[list[Any]]) -> list[str]:
    """``compare.color_map`` 的模式感知提示。

    **只在多城市对比时调用**（调用方负责判断模式）。所有提示都是**告警**、不阻断出图——
    键对不上时 core 会静默退回轮询色，属于"不生效"而非"报错"。
    """
    notes: list[str] = []
    cmpc = cfg.get("compare") or {}
    color_map = cmpc.get("color_map")
    if not isinstance(color_map, dict) or not color_map:
        return notes

    if str(cmpc.get("color_by", "order")) == "city_id":
        notes.append(
            "compare.color_by 为 city_id 时，compare.color_map 完全不生效"
            "（取色改为 colors[cityId % 长度]）；如需按城市固定颜色，请把「取色依据」改为 order"
        )
        return notes

    known = [str(name).strip() for name in (compare_cities or []) if str(name).strip()]
    if not known:
        # 拿不到本次对比城市（未解析城市名）→ 无法判断，保持沉默，避免误报
        return notes
    for key in color_map:
        text = str(key).strip()
        if text and text not in known:
            notes.append(
                f"compare.color_map 的「{key}」不在本次对比城市中"
                f"（当前：{'、'.join(known)}）：绘图时该项会被忽略、该城市退回 colors 轮询色"
            )
    return notes


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """校验一份完整表单值。

    ``payload`` 形如 ``{"profile": "简图", "values": {...}, "mode": "compare",
    "compare_cities": ["北京", "香港"]}``。返回
    ``{"ok", "errors", "warnings", "normalized", "series"}``；
    ``errors`` 与 ``warnings`` 均为 ``[{"path", "message"}]``。

    ``mode`` / ``compare_cities`` 用于**模式感知**校验：只有本次出图是多城市对比时，
    才检查 ``compare.color_map``（该键只在对比图里生效），其余模式完全忽略它。
    """
    profile = payload.get("profile") or None
    normalized, errors = normalize_values(payload.get("values") or {})

    cfg = resolve(profile, normalized)
    warnings: list[dict[str, str]] = []
    try:
        raw_warnings = schema.filter_unknown_warnings(validate_config(cfg))
    except ConfigError as exc:
        errors.append({"path": "", "message": str(exc)})
        raw_warnings = []
    warnings.extend({"path": "", "message": msg} for msg in raw_warnings)
    warnings.extend({"path": "", "message": msg} for msg in _cross_field_notes(cfg))

    mode = str(payload.get("mode") or "").strip().lower()
    if mode == "compare":
        warnings.extend(
            {"path": "compare.color_map", "message": msg}
            for msg in _compare_color_map_notes(cfg, payload.get("compare_cities"))
        )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized": normalized,
        "profile": profile,
        "series": [str(item.get("label", "")) for item in _drawn_series(cfg)],
    }


def _drawn_series(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """当前配置下「可能被绘出」的元素（仅按 enabled 与所属轴判断，不含数据可用性）。"""
    result: list[dict[str, Any]] = []
    for key, scfg in (cfg.get("series") or {}).items():
        if not isinstance(scfg, dict) or not scfg.get("enabled"):
            continue
        axis = str(scfg.get("axis", "primary"))
        axis_cfg = cfg.get(f"axes_{axis}") or {}
        if not axis_cfg.get("show", True):
            continue
        label = scfg.get("label")
        if not label or label == "auto":
            label = schema.SERIES_LABELS.get(key, key)
        result.append({"key": key, "label": label, "axis": axis})
    result.sort(key=lambda item: item["label"])
    return result


# ---- 保存与另存 ----------------------------------------------------------

def _user_comment_count(target: Path) -> int:
    """统计文件里**用户自己写的**注释行数（不含本工具生成的头部注释）。

    YAML 回写不保留注释，覆盖一个手写过的配置前需要如实告知。
    """
    try:
        text = target.read_text(encoding="utf-8-sig")
    except OSError:
        return 0
    if text.startswith(_FILE_HEADER):
        text = text[len(_FILE_HEADER):]
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("#"))


def _check_name(name: str) -> str:
    """校验配置名可用作文件名（配置名 = 文件主干，不静默改名）。"""
    raw = str(name if name is not None else "")
    text = raw.strip()
    if not text:
        raise ConfigServiceError("配置名不能为空")
    if raw != text:
        raise ConfigServiceError("配置名首尾不能有空白字符")
    if text.startswith("."):
        raise ConfigServiceError("配置名不能以点开头")
    if text.endswith("."):
        raise ConfigServiceError("配置名不能以点结尾")
    if _NAME_RE.match(text) is None:
        raise ConfigServiceError('配置名不能包含 \\ / : * ? " < > | 等字符，且不超过 60 个字符')
    if text.lower() in _RESERVED_WIN:
        raise ConfigServiceError(f"「{text}」是系统保留名，请换一个配置名")
    return text


def _safe_key(name: str) -> str:
    """文件名主干：配置名已通过 :func:`_check_name`，此处原样使用。"""
    return _check_name(name)


def _deep_get(cfg: dict[str, Any], path: str) -> Any:
    node: Any = cfg
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _has_path(cfg: dict[str, Any], path: str) -> bool:
    """该点路径是否真的存在于配置里（用于区分「键不存在」与「键的值为 null」）。"""
    node: Any = cfg
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _baseline_for(name: str, extends: Optional[str],
                  apply_styles: Optional[list[str]]) -> dict[str, Any]:
    """计算「本配置自身主体为空」时的基线（复用 core 的继承链与样式合并）。"""
    doc = load_profiles_file()
    doc = copy.deepcopy(doc)
    skeleton: dict[str, Any] = {}
    if extends:
        skeleton["extends"] = extends
    if apply_styles:
        skeleton["apply_styles"] = list(apply_styles)
    probe = f"__baseline__{name}"
    doc.setdefault("profiles", {})[probe] = skeleton
    return resolve_profile(probe, doc)


#: 只属于「运行时 / 展示」的键，不写入配置体：
#: ``profile_name`` 是只读的配置标识；``profile_description`` 会被 core 加载裸文件时
#: 直接 ``pop`` 丢弃（说明文案只认 ``description`` 键），写进去纯属噪音。
_RUNTIME_PATHS = frozenset({"profile_name", "profile_description"})


def _is_empty(value: Any) -> bool:
    return value == "" or value == [] or value == {}


def _leaf_paths(node: Any, prefix: str = "") -> list[str]:
    """把嵌套字典摊平成叶子点路径（空字典视为叶子）。"""
    if isinstance(node, dict) and node:
        paths: list[str] = []
        for key, value in node.items():
            paths.extend(_leaf_paths(value, f"{prefix}.{key}" if prefix else str(key)))
        return paths
    return [prefix] if prefix else []


def _is_noise_change(baseline_value: Any, value: Any) -> bool:
    """基线上**没有这个键**、新值又是「空语义」→ 不算修改。

    典型场景：``axes_x.label_text`` 在基线里不存在（``None``），而 ``str`` 类型会把
    ``null`` 规范化成 ``""``；``compare.color_map`` 是空对象。若不拦，保存/另存会把
    一堆空值写进配置文件。
    """
    if baseline_value is not None:
        return False
    return _is_empty(value)


def _prune_empty_extras(node: Any, prefix: str = "") -> None:
    """从「全量配置」里剔除**空值的扩展键**（Schema 之外、且基线上并不存在的那两个）。

    只动 ``schema.extra_paths()`` 里的键，其余空值（如 ``figure.legend.title: ""``）保留——
    它们是有意义的口径设置。
    """
    if not isinstance(node, dict):
        return
    extras = schema.extra_paths()
    for key in list(node):
        path = f"{prefix}.{key}" if prefix else str(key)
        value = node[key]
        if isinstance(value, dict) and value:
            _prune_empty_extras(value, path)
            if not node[key]:
                del node[key]
            continue
        if path in extras and _is_empty(value):
            del node[key]


def build_save_payload(name: str, mode: str, values: dict[str, Any],
                       base: Optional[str] = None,
                       description: Optional[str] = None) -> dict[str, Any]:
    """构造要写入 YAML 的配置主体。

    ``name``  要写入的配置名（另存时是新名称）
    ``base``  表单当前正在编辑的配置名（全量模式的解析基线、差量模式的继承目标）

    ``mode``：

    * ``"keep"``    保存修改：沿用原定义的 ``extends`` / ``apply_styles``——
                    原本是差量就继续写差量，原本是全量就写全量。
    * ``"full"``    保存全量配置：写入解析后的完整配置（可独立于任何配置使用）。
    * ``"extends"`` 继承后仅存修改部分：``extends: 基线`` + 与基线不同的项。

    ``description`` 为 ``None`` 时回退到表单里的 ``profile_description``（说明文案唯一来源）；
    想明确写成空说明，传 ``""``。
    """
    if description is None and isinstance(values, dict):
        description = values.get("profile_description")

    raw = _raw_definition(name)
    raw_extends = raw.get("extends") if isinstance(raw.get("extends"), str) else None
    raw_styles = [str(s) for s in (raw.get("apply_styles") or [])]

    keep_explicit: set[str] = set()
    if mode == "keep":
        # 该配置原本**显式写过**的项：即使与继承值相同也继续保留，
        # 避免「保存修改」把原本固化的项悄悄变成"跟随继承"
        body_only = {k: v for k, v in raw.items()
                     if k not in ("description", "extends", "apply_styles")}
        keep_explicit = set(_leaf_paths(body_only))
        if raw_extends:
            mode = "extends"
            base = raw_extends          # 保持原有的继承目标
        else:
            mode = "full"

    normalized, errors = normalize_values(values)
    if errors:
        raise ConfigServiceError(
            "配置校验未通过：" + "；".join(f"{e['path']} {e['message']}" for e in errors[:5])
        )

    if mode == "extends":
        target = str(base or raw_extends or "").strip()
        if not target:
            raise ConfigServiceError("「继承后仅存修改部分」需要指定基线配置")
        if target == name:
            raise ConfigServiceError("基线配置不能是自己")
        styles = raw_styles
        baseline = _baseline_for(name, target, styles)
        body: dict[str, Any] = {}
        for path, value in normalized.items():
            if path in _RUNTIME_PATHS:
                continue
            base_value = _deep_get(baseline, path)
            # 基线里没有、新值又是空语义 → 不写（顺带清掉历史文件里的空值噪音）
            if _is_noise_change(base_value, value):
                continue
            # 与继承值相同 → 只有原本显式写过才保留
            if value == base_value and path not in keep_explicit:
                continue
            set_by_path(body, path, copy.deepcopy(value))
        payload: dict[str, Any] = {}
        if description is not None:
            payload["description"] = str(description)
        payload["extends"] = target
        if styles:
            payload["apply_styles"] = styles
        payload = deep_merge(payload, body)
        return payload

    # 全量：以「当前编辑的配置」为基线解析完整配置
    cfg = resolve(base or name, normalized)
    body = {k: v for k, v in cfg.items()
            if not k.startswith("_") and k not in _RUNTIME_PATHS}
    _prune_empty_extras(body)
    payload = {}
    if description is not None:
        payload["description"] = str(description)
    return deep_merge(payload, body)


def save_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """保存或另存一份配置，返回 ``{"name", "path", "mode", "bytes"}``。

    说明文案（``description``）取值顺序：显式传入的 ``description`` → 表单里的
    ``profile_description``（二者都为空才不写）。这样表单中的「配置说明」与
    「另存为」弹窗里的说明文案不会互相打架。
    """
    name = _check_name(payload.get("name"))
    mode = str(payload.get("mode") or "keep").lower()
    if mode not in ("keep", "full", "extends"):
        raise ConfigServiceError(f"未知的保存方式：{mode}")
    values = payload.get("values") or {}
    base = payload.get("base")
    description = payload.get("description")

    body = build_save_payload(name, mode, values, base=base, description=description)
    text = _FILE_HEADER + yaml.safe_dump(body, **_YAML_OPTIONS)

    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    target = SAVE_DIR / f"{_safe_key(name)}.yaml"
    lost_comments = _user_comment_count(target)
    tmp = target.with_suffix(".yaml.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        raise ConfigServiceError(f"配置写入失败：{target}（{exc}）") from exc

    # 保存后确认能被 core 正常解析
    try:
        load_config(name)
    except ConfigError as exc:
        raise ConfigServiceError(f"保存的配置无法被解析（已写入 {target}）：{exc}") from exc

    return {
        "name": name,
        "path": _rel(target),
        "mode": mode,
        "bytes": len(text.encode("utf-8")),
        # 被覆盖的原文件里用户自己写的注释行数（YAML 回写不保留注释）
        "lostComments": lost_comments,
    }


def profile_file(name: str) -> Optional[Path]:
    """该配置对应的自定义文件（若存在）。"""
    if not SAVE_DIR.is_dir():
        return None
    path = SAVE_DIR / f"{_safe_key(name)}.yaml"
    return path if path.is_file() else None


def delete_profile(name: str) -> str:
    """删除网页保存的自定义配置；内置配置不可删。"""
    path = profile_file(name)
    if path is None:
        raise ConfigServiceError(f"「{name}」不是网页保存的自定义配置，无法删除")
    try:
        payload = _read_yaml_file(path)
    except ConfigServiceError as exc:
        raise ConfigServiceError(str(exc)) from exc
    wrapped = payload.get("profiles")
    if isinstance(wrapped, dict) and [str(k) for k in wrapped] not in ([], [str(name)]):
        raise ConfigServiceError(f"{_rel(path)} 内含多套配置，请手工编辑文件")
    try:
        path.unlink()
    except OSError as exc:
        raise ConfigServiceError(f"删除失败：{path}（{exc}）") from exc
    return _rel(path)


# ---- 导出 ----------------------------------------------------------------

def dump_yaml_text(data: Any) -> str:
    return yaml.safe_dump(data, **_YAML_OPTIONS)


def final_config_text(profile: Optional[str],
                      values: Optional[dict[str, Any]] = None) -> str:
    """「查看最终配置」：当前表单值套用后的完整配置（YAML）。"""
    normalized, _ = normalize_values(values or {})
    cfg = resolve(profile, normalized)
    return dump_config(cfg)


def template_text(profile: Optional[str]) -> str:
    """「导出全量模板」：带注释头的完整配置模板（YAML）。"""
    normalized, _ = normalize_values({})
    cfg = resolve(profile, normalized)
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    return _FILE_HEADER + dump_yaml_text(clean)


# ---- 面向表单的读取 ------------------------------------------------------

_ANCESTORS: Optional[set[str]] = None


def _ancestor_paths() -> set[str]:
    """Schema 里所有「中间容器」路径（如 ``series``、``fetch.cache``）。"""
    global _ANCESTORS
    if _ANCESTORS is None:
        paths = list(schema.field_index())
        ancestors: set[str] = set()
        for path in paths:
            parts = path.split(".")
            for i in range(1, len(parts)):
                ancestors.add(".".join(parts[:i]))
        _ANCESTORS = ancestors
    return _ANCESTORS


def form_values_of(cfg: dict[str, Any]) -> dict[str, Any]:
    """把配置摊平成「点路径 → 值」，覆盖 Schema 的每一个字段。

    配置里**没有**某个键时（典型是两个扩展键），给出该字段类型正确的空值
    （见 :func:`schema.empty_value`），避免表单装载后把 `null` 与 `""`/`{}` 的差异
    误判成「已修改」。
    """
    values: dict[str, Any] = {}
    for field in schema.all_fields():
        if _has_path(cfg, field.path):
            values[field.path] = _deep_get(cfg, field.path)
        else:
            values[field.path] = schema.empty_value(field)
    return values


def form_values(profile: Optional[str]) -> dict[str, Any]:
    return form_values_of(resolve(profile))


def unknown_keys(cfg: dict[str, Any]) -> list[str]:
    """配置里存在、但 Schema 未声明的键（提示用户需要手工编辑文件）。"""
    index = schema.field_index()
    containers = _ancestor_paths()
    found: list[str] = []

    def walk(node: Any, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        if prefix and index.get(prefix) is not None and index[prefix].kind in ("str_map", "json"):
            return
        for key, value in node.items():
            if str(key).startswith("_"):
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            if path not in index and path not in containers:
                found.append(path)
                continue
            walk(value, path)

    walk(cfg, "")
    return sorted(found)


def profile_detail(name: Optional[str]) -> dict[str, Any]:
    """给网页的配置详情：解析后的表单值 + 来源/继承信息 + 可用配置清单。"""
    cfg = resolve(name)
    sources = {item.name: item for item in scan_profiles()}
    raw = _raw_definition(name) if name else {}
    info = sources.get(name or "")
    return {
        "requested": name,
        "name": str(cfg.get("profile_name") or "default"),
        "description": str(cfg.get("profile_description") or ""),
        "source": info.source if info else "内置 profiles.yaml",
        "builtin": bool(info.builtin) if info else False,
        "shadowed": bool(info.shadowed) if info else False,
        "extends": raw.get("extends") if isinstance(raw.get("extends"), str) else None,
        "applyStyles": [str(s) for s in (raw.get("apply_styles") or [])],
        "values": form_values_of(cfg),
        "unknownKeys": unknown_keys(cfg),
        "available": profile_names(),
        "defaultProfile": default_profile_name(),
        "profileFile": _rel(profile_file(name)) if name and profile_file(name) else None,
    }


def apply_marks_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """应用 ``--mark`` 风格的快捷微调，返回变更说明、告警与调整后的表单值。"""
    from core.config_loader import apply_marks

    profile = payload.get("profile") or None
    normalized, errors = normalize_values(payload.get("values") or {})
    if errors:
        raise ConfigServiceError(
            "配置校验未通过：" + "；".join(f"{e['path']} {e['message']}" for e in errors[:5])
        )
    cfg = resolve(profile, normalized)
    tokens = payload.get("marks")
    raw_items: list[Any] = []
    if isinstance(tokens, str):
        raw_items = [tokens]
    elif isinstance(tokens, list):
        raw_items = [str(item) for item in tokens]
    notes, warnings = apply_marks(cfg, raw_items)
    return {
        "notes": notes,
        "warnings": warnings,
        "values": form_values_of(cfg),
    }


# ---- 缓存管理 ------------------------------------------------------------

def cache_stats() -> dict[str, Any]:
    """统计缓存占用（HTTP 响应缓存 + 城市索引缓存 + 原始响应）。"""
    from core.config_loader import PROJECT_ROOT

    cfg = resolve(None)
    dirs = {
        "响应缓存": PROJECT_ROOT / str((cfg["fetch"].get("cache") or {}).get("dir", "cache")),
        "原始响应": PROJECT_ROOT / str(cfg["fetch"].get("raw_dir", "cache/raw")),
    }
    from .state_store import STATE_NAME

    items: list[dict[str, Any]] = []
    total_files = 0
    total_bytes = 0
    for label, path in dirs.items():
        if not path.is_dir():
            items.append({"label": label, "path": _rel(path), "files": 0, "bytes": 0,
                          "exists": False})
            continue
        # 「上次使用的配置」等界面状态不是数据缓存，不计入缓存占用
        files = [p for p in path.rglob("*") if p.is_file() and p.name != STATE_NAME]
        size = sum(p.stat().st_size for p in files)
        total_files += len(files)
        total_bytes += size
        items.append({"label": label, "path": _rel(path), "files": len(files),
                      "bytes": size, "exists": True})
    index_file = None
    cache_dir = dirs["响应缓存"]
    if cache_dir.is_dir():
        candidate = cache_dir / "city_index.json"
        if candidate.is_file():
            index_file = {"path": _rel(candidate), "bytes": candidate.stat().st_size}
    return {
        "items": items,
        "files": total_files,
        "bytes": total_bytes,
        "cityIndex": index_file,
    }


def clear_cache(include_index: bool = True, include_raw: bool = True) -> dict[str, Any]:
    """清空缓存；返回删除的文件数。仅删除缓存目录内的文件，不动其他内容。"""
    from core.config_loader import PROJECT_ROOT

    cfg = resolve(None)
    removed = 0
    failed: list[str] = []

    def purge(directory: Path, pattern: str) -> None:
        nonlocal removed
        if not directory.is_dir():
            return
        for path in directory.glob(pattern):
            if not path.is_file():
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                failed.append(_rel(path))

    cache_dir = PROJECT_ROOT / str((cfg["fetch"].get("cache") or {}).get("dir", "cache"))
    purge(cache_dir, "*.bin")
    purge(cache_dir, "*.meta.json")
    if include_index:
        purge(cache_dir, "city_index.json")
    if include_raw:
        raw_dir = PROJECT_ROOT / str(cfg["fetch"].get("raw_dir", "cache/raw"))
        if raw_dir.is_dir():
            for path in raw_dir.rglob("*"):
                if path.is_file():
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        failed.append(_rel(path))
    return {"removed": removed, "failed": failed}
