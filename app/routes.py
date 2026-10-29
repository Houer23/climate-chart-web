"""HTTP 路由与 API（仅标准库）。

接口一览
--------
配置
* ``GET  /api/schema``                  配置 Schema（类型/枚举/范围/说明/分组）
* ``GET  /api/profiles``                配置清单（名称/说明/来源/是否内置）
* ``GET  /api/profiles/<名称>``         解析后的表单值与来源信息
* ``POST /api/profiles/validate``       按数据类型的逐字段校验 + 跨字段校验
* ``POST /api/profiles/save``           保存修改 / 另存（全量 · 继承差量）
* ``POST /api/profiles/delete``         删除网页保存的自定义配置
* ``POST /api/profiles/marks``          快捷微调预览（``--mark`` 语法；不改配置）
* ``POST /api/profiles/text``           查看最终配置 / 导出全量模板

城市
* ``GET  /api/cities``                  城市查询与列表（关键词 / 国家 / 条数）

出图
* ``POST /api/render``                  提交渲染任务
* ``GET  /api/render/<任务>``           任务快照（长轮询：``?cursor=N&wait=20``）
* ``GET  /api/render/<任务>/events``    实时日志流（SSE）
* ``GET  /api/render/<任务>/artifact/<产物>``  图片/表格字节流
* ``POST /api/render/<任务>/cancel``    取消任务

缓存
* ``GET  /api/cache``                   缓存占用统计
* ``POST /api/cache/clear``             清理缓存 / 城市索引 / 原始响应
"""

from __future__ import annotations

import json
import mimetypes
import re
import traceback
import urllib.parse
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Callable, Optional

from core.http_client import FetchError

from . import __version__, schema, state_store
from . import config_service as cs
from . import task_runner

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

#: 产物字节流的 Content-Type
CONTENT_TYPES = {
    "png": "image/png", "svg": "image/svg+xml", "pdf": "application/pdf",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
    "csv": "text/csv; charset=utf-8", "md": "text/markdown; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = dc_field(default_factory=dict)


@dataclass
class EventStream:
    """SSE 响应：由 server 逐帧写出。"""

    task: task_runner.Task
    cursor: int = 0
    content_type: str = "text/event-stream; charset=utf-8"


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def json_response(payload: Any, status: int = 200) -> Response:
    return Response(
        status=status,
        body=json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8"),
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


# ---- 路由表 --------------------------------------------------------------

ROUTES: list[tuple[str, re.Pattern, Callable[[dict], Any]]] = []


def route(method: str, pattern: str):
    compiled = re.compile(f"^{pattern}$")

    def decorator(func: Callable[[dict], Any]) -> Callable[[dict], Any]:
        ROUTES.append((method.upper(), compiled, func))
        return func

    return decorator


def dispatch(method: str, path: str, query: dict[str, list[str]],
             body: Any, headers: dict[str, str]) -> Any:
    for route_method, pattern, handler in ROUTES:
        if route_method != method.upper():
            continue
        match = pattern.match(path)
        if match is None:
            continue
        ctx = {
            "path": path,
            "query": query,
            "body": body if body is not None else {},
            "params": match.groupdict(),
            "headers": headers,
        }
        return handler(ctx)
    raise ApiError(404, f"没有这个接口：{method} {path}")


# ---- 配置 ----------------------------------------------------------------

@route("GET", r"/api/schema")
def api_schema(_ctx: dict) -> Response:
    payload = {"version": __version__, "defaultProfile": cs.default_profile_name()}
    payload.update(schema.schema_payload())
    return json_response(payload)


@route("GET", r"/api/state")
def api_state(_ctx: dict) -> Response:
    """网页端持久化状态 + 本次启动应选用的配置。

    ``profile`` 是服务端算好的结果：上次使用的配置**仍然存在**就用它，
    否则回退到默认配置（默认配置也不存在时取清单里的第一个）。
    刷新 / 重开页面时前端直接用它即可，前端只保留一层存在性兜底。
    """
    data = state_store.read_state()
    names = cs.profile_names()
    default = cs.default_profile_name()
    last = data["last_profile"]
    resolved = cs.pick_profile(last, names, default)
    return json_response({
        "lastProfile": last,
        "profile": resolved,
        "isLastUsed": bool(last) and resolved == last,
        # 左右分栏左栏占比（0 = 未设置，由前端用默认值）
        "leftRatio": data["left_ratio"],
        "updatedAt": data["updated_at"],
        "defaultProfile": default,
        "available": names,
    })


@route("POST", r"/api/state")
def api_state_update(ctx: dict) -> Response:
    body = ctx["body"]
    data = state_store.read_state()
    if "lastProfile" in body and body["lastProfile"] is not None:
        data = state_store.remember_profile(body["lastProfile"])
    if "leftRatio" in body and body["leftRatio"] is not None:
        data = state_store.remember_ratio(body["leftRatio"])
    return json_response({"lastProfile": data["last_profile"],
                          "leftRatio": data["left_ratio"],
                          "updatedAt": data["updated_at"]})


@route("GET", r"/api/profiles")
def api_profiles(_ctx: dict) -> Response:
    return json_response({
        "profiles": [item.to_dict() for item in cs.scan_profiles()],
        "defaultProfile": cs.default_profile_name(),
        "saveDir": "config/custom",
    })


@route("GET", r"/api/profiles/(?P<name>[^/]+)")
def api_profile_detail(ctx: dict) -> Response:
    name = urllib.parse.unquote(ctx["params"]["name"])
    return json_response(cs.profile_detail(name))


@route("POST", r"/api/profiles/validate")
def api_validate(ctx: dict) -> Response:
    return json_response(cs.validate_payload(ctx["body"]))


@route("POST", r"/api/profiles/save")
def api_save(ctx: dict) -> Response:
    saved = cs.save_profile(ctx["body"])
    detail = cs.profile_detail(saved["name"])
    return json_response({"saved": saved, "profile": detail})


@route("POST", r"/api/profiles/delete")
def api_delete(ctx: dict) -> Response:
    name = str(ctx["body"].get("name") or "")
    removed = cs.delete_profile(name)
    return json_response({"removed": removed, "profiles": [i.to_dict() for i in cs.scan_profiles()]})


@route("POST", r"/api/profiles/marks")
def api_marks(ctx: dict) -> Response:
    return json_response(cs.apply_marks_payload(ctx["body"]))


@route("POST", r"/api/profiles/text")
def api_text(ctx: dict) -> Response:
    body = ctx["body"]
    profile = body.get("profile")
    kind = str(body.get("kind") or "final").lower()
    if kind == "template":
        text = cs.template_text(profile)
    else:
        text = cs.final_config_text(profile, body.get("values") or {}, body.get("marks"))
    return json_response({"kind": kind, "text": text})


# ---- 城市 ----------------------------------------------------------------

#: 城市索引内存缓存：网页里输入城市时会反复解析，没必要每次都重读 1MB JSON
_INDEX_MEMO: dict[str, Any] = {"index": None, "key": ""}


def _city_index(cfg: dict[str, Any], refresh: bool = False):
    """取城市索引（带内存缓存，避免每次输入都重建 3629 个城市对象）。"""
    from core.city_index import CityLookupError, load_city_index
    from core.pipeline import make_client, paths_for

    cache_file = paths_for(cfg)["cache_dir"] / "city_index.json"
    memo_key = str(cache_file)
    if not refresh and _INDEX_MEMO["index"] is not None and _INDEX_MEMO["key"] == memo_key:
        return _INDEX_MEMO["index"]

    client = make_client(cfg)
    try:
        index = load_city_index(client, cfg, cache_file, refresh=refresh)
    except FetchError as exc:
        raise ApiError(502, f"城市索引获取失败：{exc}") from exc
    except CityLookupError as exc:
        raise ApiError(502, f"城市索引不可用：{exc}") from exc
    _INDEX_MEMO["index"] = index
    _INDEX_MEMO["key"] = memo_key
    return index


@route("GET", r"/api/cities")
def api_cities(ctx: dict) -> Response:
    query = ctx["query"]
    keyword = _first(query, "q")
    country = _first(query, "country")
    limit = _int_param(query, "limit", 50, 1, 500)
    profile = _first(query, "profile")
    cfg = cs.resolve(profile)
    index = _city_index(cfg, refresh=_first(query, "refresh") not in ("", "0", "false"))

    # 逐项解析（cityId 或城市名）→ 城市清单，用于「指定城市颜色」的键候选
    tokens = _first(query, "resolve")
    if tokens:
        return json_response(_resolve_cities(index, cfg, tokens))

    if country:
        entries = index.list_by_country(country)[:limit]
    elif keyword:
        entries = index.search(keyword, limit=limit)
    else:
        entries = index.entries[:limit]
    from core.models import city_display_name, coord_pair

    return json_response({
        "total": len(entries),
        "limit": limit,
        "cities": [
            {
                "cityId": entry.city_id,
                "name": entry.city_name,
                "display": city_display_name(entry, cfg),
                "memName": entry.mem_name,
                "station": entry.station_name,
                "coords": coord_pair(entry.latitude, entry.longitude, cfg),
            }
            for entry in entries
        ],
    })


def _resolve_cities(index, cfg: dict[str, Any], raw: str) -> dict[str, Any]:
    """把「cityId 或城市名」逐项解析为城市（含**显示名**）。

    显示名就是 ``compare.color_map`` 需要的键（受 ``data.city_short_name`` 影响），
    因此前端可以直接拿它做下拉候选，避免手输城市名出错。
    """
    from core.city_index import CityLookupError
    from core.models import city_display_name, coord_pair

    items: list[dict[str, Any]] = []
    for token in [piece.strip() for piece in str(raw).split(",") if piece.strip()]:
        entry = index.by_id(int(token)) if token.isdigit() else None
        if entry is None:
            try:
                entry = index.resolve_one(token)
            except CityLookupError:
                entry = None
        if entry is None:
            items.append({"input": token, "ok": False, "error": "未找到匹配城市"})
            continue
        items.append({
            "input": token,
            "ok": True,
            "cityId": entry.city_id,
            "name": entry.city_name,
            "display": city_display_name(entry, cfg),
            "memName": entry.mem_name,
            "coords": coord_pair(entry.latitude, entry.longitude, cfg),
        })
    return {"resolved": items}


def _first(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return values[0].strip() if values else ""


def _int_param(query: dict[str, list[str]], key: str, default: int,
               low: int, high: int) -> int:
    raw = _first(query, key)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(low, min(high, value))


# ---- 出图 ----------------------------------------------------------------

@route("POST", r"/api/render")
def api_render(ctx: dict) -> Response:
    body = ctx["body"]
    profile = body.get("profile")
    values = body.get("values") or {}
    # 带 mode / compare_cities 一起校验：compare.color_map 只在多城市对比时才检查
    checked = cs.validate_payload({
        "profile": profile,
        "values": values,
        "mode": body.get("mode"),
        "compare_cities": body.get("compare_cities"),
    })
    if not checked["ok"]:
        raise ApiError(400, "配置校验未通过，请先修正配置", errors=checked["errors"],
                       warnings=checked["warnings"])

    cfg = cs.resolve(profile, checked["normalized"])
    cfg = task_runner.apply_request_overrides(cfg, body)
    # 快捷微调（--mark）：只作用在本次出图的配置副本上，不写回表单/配置文件
    marks = cs.mark_items(body.get("marks"))
    mark_notes, mark_warnings = task_runner.apply_marks_overrides(cfg, marks)
    mark_warnings.extend(_mark_cross_warnings(marks, body.get("mode")))
    request = dict(body)
    if "save_files" not in request:
        request["save_files"] = not bool(request.get("no_save"))
    task = task_runner.submit(request, cfg)
    for note in mark_notes:
        task.log("info", note)
    for item in mark_warnings:
        task.log("warning", item)
    return json_response({
        "task": task.snapshot(0),
        "warnings": checked["warnings"],
        "marks": {"applied": marks, "notes": mark_notes, "warnings": mark_warnings},
    })


def _mark_cross_warnings(marks: list[str], mode: Any) -> list[str]:
    """跨字段提醒：``cy`` / ``cn`` 会改变城市显示名，对比图的颜色键按显示名匹配。"""
    if not marks or str(mode or "").lower() != "compare":
        return []
    from core.config_loader import split_marks

    if any(token.lower() in ("cy", "cn") for token in split_marks(marks)):
        return ["微调改变了城市显示名，「指定城市颜色」的键可能与显示名对不上"]
    return []


@route("GET", r"/api/render/(?P<task>[0-9a-zA-Z]+)")
def api_render_status(ctx: dict) -> Response:
    task = task_runner.get(ctx["params"]["task"])
    if task is None:
        raise ApiError(404, "任务不存在或已过期")
    cursor = _int_param(ctx["query"], "cursor", 0, 0, 10 ** 9)
    wait = _int_param(ctx["query"], "wait", 0, 0, 60)
    if wait <= 0:
        return json_response(task.snapshot(cursor))
    entries, next_cursor, status = task.wait_for_logs(cursor, timeout=float(wait))
    payload = task.snapshot(next_cursor)
    payload["logs"] = entries
    payload["cursor"] = next_cursor
    payload["status"] = status
    return json_response(payload)


@route("GET", r"/api/render/(?P<task>[0-9a-zA-Z]+)/events")
def api_render_events(ctx: dict) -> EventStream:
    task = task_runner.get(ctx["params"]["task"])
    if task is None:
        raise ApiError(404, "任务不存在或已过期")
    cursor = _int_param(ctx["query"], "cursor", 0, 0, 10 ** 9)
    return EventStream(task=task, cursor=cursor)


@route("GET", r"/api/render/(?P<task>[0-9a-zA-Z]+)/artifact/(?P<art>[0-9a-zA-Z\-]+)")
def api_artifact(ctx: dict) -> Response:
    task = task_runner.get(ctx["params"]["task"])
    if task is None:
        raise ApiError(404, "任务不存在或已过期")
    artifact = task.artifacts.get(ctx["params"]["art"])
    if artifact is None:
        raise ApiError(404, "产物不存在")
    fmt = artifact.fmt.lower()
    download = _first(ctx["query"], "download") not in ("", "0", "false")
    if artifact.kind == "table" and "inline" not in ctx["query"]:
        download = True
    headers: dict[str, str] = {}
    if download:
        quoted = urllib.parse.quote(artifact.name)
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quoted}"
    return Response(
        body=artifact.data,
        content_type=CONTENT_TYPES.get(fmt, "application/octet-stream"),
        headers=headers,
    )


@route("POST", r"/api/render/(?P<task>[0-9a-zA-Z]+)/cancel")
def api_cancel(ctx: dict) -> Response:
    task_id = ctx["params"]["task"]
    ok = task_runner.cancel(task_id)
    task = task_runner.get(task_id)
    return json_response({"cancelled": ok, "task": task.snapshot(0) if task else None})


# ---- 缓存 ----------------------------------------------------------------

@route("GET", r"/api/cache")
def api_cache(_ctx: dict) -> Response:
    return json_response(cs.cache_stats())


@route("POST", r"/api/cache/clear")
def api_cache_clear(ctx: dict) -> Response:
    body = ctx["body"]
    result = cs.clear_cache(
        include_index=bool(body.get("index", True)),
        include_raw=bool(body.get("raw", True)),
    )
    if body.get("index", True):
        _INDEX_MEMO["index"] = None      # 城市索引文件已被删除，内存缓存同步失效
        _INDEX_MEMO["key"] = ""
    result["stats"] = cs.cache_stats()
    return json_response(result)


# ---- 静态资源 ------------------------------------------------------------

@route("GET", r"/")
def index(_ctx: dict) -> Response:
    return _static_file(WEB_DIR / "index.html")


@route("GET", r"/static/(?P<rel>.+)")
def static(ctx: dict) -> Response:
    rel = urllib.parse.unquote(ctx["params"]["rel"])
    candidate = (WEB_DIR / rel).resolve()
    if not str(candidate).startswith(str(WEB_DIR.resolve())):
        raise ApiError(403, "路径越界")
    return _static_file(candidate)


@route("GET", r"/favicon.ico")
def favicon(_ctx: dict) -> Response:
    return Response(status=204, body=b"")


def _static_file(path: Path) -> Response:
    if not path.is_file():
        raise ApiError(404, f"文件不存在：{path.name}")
    mime, _ = mimetypes.guess_type(str(path))
    if mime is None:
        mime = "application/octet-stream"
    if mime.startswith("text/") or mime in ("application/javascript", "image/svg+xml"):
        mime = f"{mime}; charset=utf-8"
    return Response(body=path.read_bytes(), content_type=mime,
                    headers={"Cache-Control": "no-cache"})


# ---- 统一异常转换 --------------------------------------------------------

def to_response(exc: Exception) -> Response:
    """把内部异常转成 JSON 错误响应。"""
    if isinstance(exc, ApiError):
        payload = {"error": exc.message, **exc.extra}
        return json_response(payload, status=exc.status)
    if isinstance(exc, cs.ConfigServiceError):
        return json_response({"error": str(exc)}, status=400)
    if isinstance(exc, task_runner.RenderError):
        return json_response({"error": str(exc)}, status=400)
    if isinstance(exc, schema.FieldError):
        return json_response({"error": str(exc), "path": exc.path}, status=400)
    return json_response({
        "error": f"服务端异常：{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc().splitlines()[-6:],
    }, status=500)
