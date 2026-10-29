"""接口端到端测试（真实 HTTP，含 SSE 与出图）。

覆盖：Schema / 配置清单 / 配置详情 / 校验 / 出图（保存与不保存两种）/ 产物下载 /
实时日志流 / 缓存统计。

需要联网取一次北京（cityId 237）的气候数据（命中缓存则不联网）。
运行：``python tests/test_api.py``
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config_service as cs  # noqa: E402
from app.server import Handler, Server  # noqa: E402
from core.pipeline import fetch_city as core_fetch_city  # noqa: E402
from core.pipeline import make_client as core_make_client  # noqa: E402


class QuietHandler(Handler):
    """测试时不要往 stderr 打访问日志。"""

    def log_message(self, fmt, *args):  # noqa: A003
        return


PASSED = 0
FAILED: list[str] = []


def check(condition: bool, message: str) -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(message)


def profile_path(name: str) -> str:
    return "/api/profiles/" + urllib.parse.quote(name)


httpd = Server(("127.0.0.1", 0), QuietHandler)
PORT = httpd.server_address[1]
BASE = f"http://127.0.0.1:{PORT}"
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def call(method: str, path: str, payload: dict | None = None, timeout: float = 120.0):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, response.headers, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def api(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    status, _headers, raw = call(method, path, payload)
    try:
        return status, json.loads(raw.decode("utf-8"))
    except ValueError:
        return status, {"_raw": raw[:200].decode("utf-8", "replace")}


# ---- 静态资源 ------------------------------------------------------------

status, headers, raw = call("GET", "/")
check(status == 200 and b"<!DOCTYPE html" in raw[:200], "首页应返回 HTML")
status, headers, raw = call("GET", "/static/js/app.js")
check(status == 200 and b"use strict" in raw, "静态资源应可访问")
status, _, _ = call("GET", "/static/../run.py")
check(status in (403, 404), f"静态资源不得越界访问（实际 {status}）")

# ---- Schema 与配置 -------------------------------------------------------

status, payload = api("GET", "/api/schema")
check(status == 200, "GET /api/schema 应成功")
groups = {g["key"]: g for g in payload.get("groups") or []}
check(len(groups) >= 13, f"Schema 分组数应 ≥ 13，实际 {len(groups)}")
paths = [f["path"] for g in payload["groups"] for f in g["fields"]]
check("data.temp_unit" in paths and "multi.grid" in paths, "Schema 应含关键配置项")
check(payload["defaultProfile"] == "default", "Schema 应带默认配置名")
check(len(paths) >= 460, f"Schema 字段总数应 ≥ 460，实际 {len(paths)}")
check(len(set(paths)) == len(paths), "Schema 字段路径不应重复")

# 分组展开：绘图元素按元素展开、横轴带前缀、配置名只读
series_group = groups["series"]
series_template = series_group.get("seriesFields") or []
check(len(series_template) >= 25,
      f"绘图元素模板字段应 ≥ 25 个，实际 {len(series_template)}")
check(len(series_group["fields"]) == 5 * len(series_template),
      f"绘图元素应展开为 5×{len(series_template)} 个字段，"
      f"实际 {len(series_group['fields'])}")
check(len(series_group.get("seriesKeys") or []) == 5, "绘图元素分组应给出 5 个元素键")
check(all(f["path"].startswith("series.") for f in series_group["fields"]),
      "绘图元素字段应带 series.<元素>. 前缀")
check(all(f["path"].startswith("axes_x.") for f in groups["axes_x"]["fields"]),
      "横轴字段应带 axes_x. 前缀")
check(any(f["path"] == "axes_tertiary.offset_points" for f in groups["axes_tertiary"]["fields"]),
      "第三轴应含 offset_points")
check(not any(f["path"] == "axes_primary.offset_points" for f in groups["axes_primary"]["fields"]),
      "主轴不应含 offset_points")
name_field = [f for f in groups["meta"]["fields"] if f["path"] == "profile_name"][0]
check(name_field["readonly"] is True, "profile_name 应为只读")
enum_field = [f for f in groups["data"]["fields"] if f["path"] == "data.temp_unit"][0]
check(enum_field["kind"] == "enum" and len(enum_field["enum"]) == 2,
      "data.temp_unit 应是 2 选项枚举")
template_field = [f for f in groups["figure"]["fields"] if f["path"] == "figure.title.text"][0]
check(template_field["kind"] == "template" and "city" in template_field["placeholders"],
      "标题文本应是模板字段并给出占位符")
check(any(f["extra"] for f in groups["compare"]["fields"]),
      "对比分组应包含扩展项 color_map")
compare_fields = {f["path"]: f for f in groups["compare"]["fields"]}
color_map_field = compare_fields["compare.color_map"]
check(color_map_field["keyKind"] == "city" and color_map_field["valueKind"] == "color",
      "color_map 应声明「键=城市、值=颜色」，前端才能给下拉")
check(len(color_map_field["valueOptions"]) >= 8,
      f"color_map 应提供 ≥8 个预设颜色，实际 {len(color_map_field['valueOptions'])}")
check(color_map_field["extra"] is True, "color_map 应标记为扩展键")

check(compare_fields["compare.metrics"]["maxItems"] == 4,
      "compare.metrics 应向前端暴露 maxItems=4")
legend_fields = [f for f in groups["figure"]["fields"] if f["path"].startswith("figure.legend.")]
check(any(item["value"] == "legend" for item in groups["figure"].get("subgroups") or []),
      "画布分组应把图例参数单独成组")
check(len(legend_fields) >= 14 and all(f["subgroup"] == "legend" for f in legend_fields),
      f"figure.legend.* 应全部归入「图例」子组，实际 {len(legend_fields)} 项")
compare_sub_keys = [item["value"] for item in groups["compare"].get("subgroups") or []]
check(all(key in compare_sub_keys
          for key in ("minTemp", "maxTemp", "meanTemp", "rainfall", "raindays")),
      f"compare 分组应保留 5 个元素样式子分组，实际 {compare_sub_keys}")
check({"basic", "layout", "color", "style", "legend"} <= set(compare_sub_keys),
      f"compare 分组应按对象给出小节，实际 {compare_sub_keys}")
check(any(f["subgroup"] for f in groups["compare"]["fields"]),
      "元素样式字段应带 subgroup，前端才能渲染成 5 个小组")

# 二级/三级分层：除 meta 外每个大类都要有子分组；series 用「元素|属性族」表示三级
empties = [group_key for group_key, group in groups.items()
           if group_key not in ("meta", "series") and not (group.get("subgroups") or [])]
check(not empties, f"除 meta / series 外每个大类都应有子分组，实际缺：{empties}")
check(bool(groups["series"].get("seriesKeys")),
      "series 分组应以元素（seriesKeys）作为一级分组")
check(len(groups["figure"].get("subgroups") or []) >= 10,
      f"画布分组应按对象拆成 ≥10 个小节，实际 {len(groups['figure'].get('subgroups') or [])}")
series_fields_all = groups["series"]["fields"]
check(all("|" in f["subgroup"] for f in series_fields_all),
      "series 字段的 subgroup 应是「元素|属性族」形式（三级分组）")
check(all(f["subgroupLabel"] for f in series_fields_all),
      "series 字段应带 subgroupLabel，供三级小节标题使用")
series_families = {f["subgroup"].split("|")[1] for f in series_fields_all}
check({"basic", "line", "bar", "labels"} <= series_families,
      f"series 属性族应至少含 基础/线/柱/标签，实际 {sorted(series_families)}")

status, payload = api("GET", "/api/profiles")
check(status == 200 and any(p["name"] == "default" for p in payload["profiles"]),
      "配置清单应含 default")
check(any(p["name"] == "简2" and p["shape"] == "single" for p in payload["profiles"]),
      "配置清单应识别单配置裸文件")

# 上次使用的配置（刷新 / 重开自动选中；失效则回退默认）
status, payload = api("GET", "/api/state")
check(status == 200 and "lastProfile" in payload, f"GET /api/state 应可用，实际 {status}")
check(payload["defaultProfile"] == "default", "状态应带默认配置名（供回退）")
check("简2" in (payload.get("available") or []),
      "状态应返回可用配置清单（前端据此判断上次的配置是否还在）")
original_state = payload["lastProfile"]

status, payload = api("POST", "/api/state", {"lastProfile": "简2"})
check(status == 200 and payload["lastProfile"] == "简2", "应记住上次使用的配置")
status, payload = api("GET", "/api/state")
check(payload["lastProfile"] == "简2", "重新读取应与写入一致")
check(bool(payload["updatedAt"]), "应记录更新时间")
check(payload["profile"] == "简2" and payload["isLastUsed"] is True,
      f"上次使用的配置仍存在时应选它，实际 {payload.get('profile')}")
check(cs.pick_profile(payload["lastProfile"], payload["available"],
                      payload["defaultProfile"]) == payload["profile"],
      "服务端选择结果应与 pick_profile 规则一致")

# 记录了一个已不存在的配置 → 应回退默认
api("POST", "/api/state", {"lastProfile": "早就删掉的配置"})
status, payload = api("GET", "/api/state")
check(payload["profile"] == "default" and payload["isLastUsed"] is False,
      f"上次的配置不存在时应回退默认，实际 {payload.get('profile')}")

# 清除记录 → 应回退默认
api("POST", "/api/state", {"lastProfile": ""})
status, payload = api("GET", "/api/state")
check(payload["lastProfile"] == "" and payload["profile"] == "default",
      "未记录时应选默认配置")

api("POST", "/api/state", {"lastProfile": original_state})   # 还原，不影响真实使用
status, payload = api("GET", "/api/state")
check(payload["lastProfile"] == original_state, "应可还原为原值")

# 左右分栏比例（拖动分隔条后的宽度，服务端只负责记住）
status, payload = api("POST", "/api/state", {"leftRatio": 0.42})
check(abs(payload["leftRatio"] - 0.42) < 1e-9, f"应记住分栏比例，实际 {payload}")
status, payload = api("GET", "/api/state")
check(abs(payload["leftRatio"] - 0.42) < 1e-9, "重新读取应一致")
api("POST", "/api/state", {"leftRatio": "坏值"})
status, payload = api("GET", "/api/state")
check(payload["leftRatio"] == 0.0, "非法比例应被丢弃")
# 写比例不应影响「上次使用的配置」
check(payload["lastProfile"] == original_state, "两个状态字段应互不干扰")
api("POST", "/api/state", {"leftRatio": 0})   # 还原

status, detail = api("GET", "/api/profiles/default")
check(status == 200, "GET /api/profiles/default 应成功")
check(len(detail["values"]) >= 450, f"表单值应覆盖全部字段，实际 {len(detail['values'])}")
check(detail["builtin"] is True, "default 应标记为内置")
check(detail["unknownKeys"] == [], f"default 不应有未知键，实际 {detail['unknownKeys']}")

status, detail2 = api("GET", profile_path("简2"))
check(detail2["extends"] == "简图", f"简2 应继承简图，实际 {detail2['extends']}")
check(detail2["values"]["figure.mean_rain_line.show"] is True,
      "简2 的表单值应反映继承链合并结果")

status, payload = api("GET", profile_path("不存在的配置"))
check(status == 200 and payload["name"] == "default",
      "未知配置应回退到默认配置（不报错）")

# ---- 校验 ----------------------------------------------------------------

status, payload = api("POST", "/api/profiles/validate", {
    "profile": "default", "values": {"data.temp_unit": "K", "output.chart_dpi": -5}})
check(status == 200 and payload["ok"] is False and len(payload["errors"]) == 2,
      f"非法值应报 2 个错误，实际 {payload}")

status, payload = api("POST", "/api/profiles/validate", {
    "profile": "default", "values": {"multi.grid": "2×2", "figure.figsize": [12, 6]}})
check(payload["ok"] and payload["normalized"]["multi.grid"] == "2x2",
      f"合法值应通过并归一化，实际 {payload}")

# 模式感知：compare.color_map 只在多城市对比时校验
status, payload = api("POST", "/api/profiles/validate", {
    "profile": "default", "mode": "compare",
    "values": {"compare.color_map": {"北京": "#d85a30", "火星": "#185fa5"}},
    "compare_cities": ["北京", "香港"],
})
check(payload["ok"], "color_map 键未命中不应阻断校验")
check(any(w["path"] == "compare.color_map" and "火星" in w["message"]
          for w in payload["warnings"]),
      f"对比模式下应提示未命中的城市，实际 {payload['warnings']}")

status, payload = api("POST", "/api/profiles/validate", {
    "profile": "default", "mode": "city",
    "values": {"compare.color_map": {"火星": "#185fa5"}},
})
check(payload["ok"] and not any("color_map" in w["message"] for w in payload["warnings"]),
      f"单城模式应完全忽略 color_map，实际 {payload['warnings']}")

status, payload = api("POST", "/api/profiles/validate", {
    "profile": "default", "mode": "compare",
    "values": {"compare.color_map": {"北京": "浅蓝"}},
    "compare_cities": ["北京"],
})
check(not payload["ok"] and any(e["path"].startswith("compare.color_map")
                                for e in payload["errors"]),
      f"非法颜色值应是字段错误，实际 {payload['errors']}")

status, payload = api("POST", "/api/profiles/marks", {
    "profile": "default", "values": {}, "marks": "hh,r12,t14"})
check(status == 200 and payload["notes"], f"快捷微调应返回变更说明，实际 {payload}")
check("values" not in payload,
      "快捷微调预览不应返回整份表单值（否则前端会写回表单、制造「未保存修改」）")
changes = {item["path"]: item for item in payload.get("changes") or []}
check(changes.get("axes_primary.limit_shift", {}).get("after") == 20,
      f"hh 应把气温轴整体上移 20，实际 {changes.get('axes_primary.limit_shift')}")
check(changes.get("axes_secondary.limit", {}).get("after") == [0, 250],
      f"r12 应把降水轴上限设为 250，实际 {changes.get('axes_secondary.limit')}")
check(changes.get("axes_primary.label_fontsize", {}).get("after") == 14,
      f"t14 应把气温轴标题字号设为 14，实际 {changes.get('axes_primary.label_fontsize')}")

status, payload = api("POST", "/api/profiles/text", {"profile": "default", "kind": "final"})
check(status == 200 and "temp_unit" in payload["text"], "查看最终配置应返回 YAML")

status, payload = api("POST", "/api/profiles/text",
                      {"profile": "default", "kind": "final", "marks": "hh"})
check(status == 200 and "limit_shift: 20" in payload["text"],
      f"「最终配置」应包含本次微调（hh → limit_shift: 20），实际前缀 {payload['text'][:80]!r}")

# ---- 出图（保存模式） ----------------------------------------------------

FAST = {"fetch.min_interval": 0.0, "output.chart_formats": ["png"],
        "output.table_formats": ["csv"], "output.overwrite": "timestamp",
        "output.log_level": "DEBUG"}

# 预热缓存：让下面「命中缓存」的断言确定成立，不依赖外部缓存是否还在有效期内
prime_cfg = cs.resolve("default")
prime_cfg["fetch"]["min_interval"] = 0.0
prime_cfg["fetch"]["cache"]["enabled"] = True
core_fetch_city(core_make_client(prime_cfg), prime_cfg, 237)

status, payload = api("POST", "/api/render", {
    "mode": "city", "cities": ["237"], "profile": "default",
    "values": FAST, "save_files": True,
})
check(status == 200, f"提交渲染应成功，实际 {status} {payload}")
task_id = (payload.get("task") or {}).get("id")
check(bool(task_id), "提交渲染应返回任务号")

deadline = time.time() + 180
snapshot = {}
while time.time() < deadline:
    status, snapshot = api("GET", f"/api/render/{task_id}?cursor=0&wait=10")
    if snapshot.get("status") in ("done", "error", "cancelled"):
        break
check(snapshot.get("status") == "done", f"渲染任务应完成，实际 {snapshot.get('status')}")
messages = [log["message"] for log in snapshot["logs"]]
check(any("命中缓存" in m for m in messages),
      f"已预热缓存时应命中缓存，实际 {messages[:6]}")
check(any("请求统计" in m for m in messages), "日志应包含请求统计")

artifacts = snapshot.get("artifacts") or []
charts = [a for a in artifacts if a["kind"] == "chart" and a["fmt"] == "png"]
tables = [a for a in artifacts if a["kind"] == "table"]
check(charts, f"应产出 PNG 图表，实际 {[a['kind'] for a in artifacts]}")
check(tables, "应产出表格")
check(all(a["saved"] for a in charts + tables), "保存模式下产物应标记为已落盘")
check((snapshot.get("result") or {}).get("summary", "").startswith("成功 1 个"),
      f"结果摘要应显示成功 1 个，实际 {snapshot.get('result', {}).get('summary')}")
city_result = (snapshot["result"].get("cities") or [{}])[0]
check(city_result.get("series"), "结果应列出绘出要素")

saved_paths = [a["path"] for a in charts + tables]
check(all(Path(p).is_file() for p in saved_paths), f"落盘文件应真实存在：{saved_paths}")

status, headers, raw = call("GET", charts[0]["url"])
check(status == 200 and raw[:8] == b"\x89PNG\r\n\x1a\n", "产物接口应返回合法 PNG")
status, headers, raw = call("GET", charts[0]["url"] + "?download=1")
check("attachment" in (headers.get("Content-Disposition") or ""),
      "download=1 应带下载头")
status, _headers, raw = call("GET", tables[0]["url"])
check(b"\xef\xbb\xbf" in raw[:6], "CSV 表格应带 UTF-8 BOM")

# 清理这次落盘的文件
for path in saved_paths:
    try:
        Path(path).unlink()
    except OSError:
        pass

# ---- 出图（不保存开关） --------------------------------------------------

out_dir = ROOT / "output"
before = {p for p in out_dir.rglob("*") if p.is_file()} if out_dir.is_dir() else set()

status, payload = api("POST", "/api/render", {
    "mode": "city", "cities": ["237"], "profile": "default",
    "values": FAST, "save_files": False, "refresh_cache": True,
})
temp_id = (payload.get("task") or {}).get("id")
deadline = time.time() + 180
snapshot = {}
while time.time() < deadline:
    status, snapshot = api("GET", f"/api/render/{temp_id}?cursor=0&wait=10")
    if snapshot.get("status") in ("done", "error", "cancelled"):
        break
check(snapshot.get("status") == "done", f"临时出图应完成，实际 {snapshot.get('status')}")
after = {p for p in out_dir.rglob("*") if p.is_file()} if out_dir.is_dir() else set()
check(before == after, f"「不保存」模式不应新增本地文件：{sorted(str(p) for p in after - before)}")
check(all(not a["saved"] for a in snapshot["artifacts"]),
      "「不保存」模式产物不应标记为已落盘")
check(snapshot["result"]["output_dir"] is None, "「不保存」模式不应给出输出目录")
temp_messages = [log["message"] for log in snapshot["logs"]]
check(any("请求：" in m for m in temp_messages),
      f"忽略缓存时应能看到请求进度（请求等待可见），实际 {temp_messages[:6]}")
check(any("完成（" in m for m in temp_messages),
      "DEBUG 级日志也应保留（完成信息含状态码/字节数/耗时）")
temp_chart = [a for a in snapshot["artifacts"] if a["kind"] == "chart"][0]
status, _headers, raw = call("GET", temp_chart["url"])
check(status == 200 and raw[:8] == b"\x89PNG\r\n\x1a\n",
      "「不保存」模式的预览图仍应可显示")

# ---- 快捷微调（--mark）：只作用本次出图，不改配置 ------------------------

status, payload = api("POST", "/api/render", {
    "mode": "city", "cities": ["237"], "profile": "default",
    "values": FAST, "save_files": False, "marks": "hh,r12,t14",
})
mark_info = (payload or {}).get("marks") or {}
check(status == 200 and mark_info.get("notes"),
      f"带微调的出图应提交成功并回变更说明，实际 {status} {payload}")
mark_id = (payload.get("task") or {}).get("id")
deadline = time.time() + 180
snapshot = {}
while time.time() < deadline:
    status, snapshot = api("GET", f"/api/render/{mark_id}?cursor=0&wait=10")
    if snapshot.get("status") in ("done", "error", "cancelled"):
        break
check(snapshot.get("status") == "done", f"带微调的任务应完成，实际 {snapshot.get('status')}")
mark_messages = [log["message"] for log in snapshot.get("logs") or []]
check(any(m.startswith("--mark：") for m in mark_messages),
      f"任务日志应包含 --mark 变更说明，实际 {mark_messages[:8]}")
check(all(not a["saved"] for a in snapshot.get("artifacts") or []),
      "带微调且「不保存」时不应落盘")

status, detail_after = api("GET", "/api/profiles/default")
check(detail_after["values"]["axes_primary.limit_shift"]
      == detail["values"]["axes_primary.limit_shift"]
      and detail_after["values"]["axes_secondary.limit"]
      == detail["values"]["axes_secondary.limit"],
      "微调不应改动配置：出图后配置详情应保持原值")

# ---- SSE 实时日志流 ------------------------------------------------------

request = urllib.request.Request(BASE + f"/api/render/{temp_id}/events?cursor=0")
with urllib.request.urlopen(request, timeout=30) as response:
    check(response.headers.get("Content-Type", "").startswith("text/event-stream"),
          "SSE 应返回 text/event-stream")
    try:
        raw = response.read(4096)
    except http.client.IncompleteRead as exc:
        raw = exc.partial
frames = raw.count(b"data: ")
check(frames >= 1, f"SSE 应至少推送一帧，实际 {frames}")
check(b'"status"' in raw or b'"type"' in raw, f"SSE 帧应含状态信息，实际 {raw[:120]!r}")

# 柱填充方式（纹理）应能通过接口出图
status, payload = api("POST", "/api/render", {
    "mode": "city", "cities": ["237"], "profile": "default",
    "values": {**FAST, "series.rainfall.bar_fill": "hatch",
               "series.rainfall.bar_hatch": "///",
               "series.rainfall.bar_fill_color": "#dbe6f0",
               "output.name_template": "_apitest_bar_{profile}"},
    "save_files": True,
})
bar_id = (payload.get("task") or {}).get("id")
snapshot = {}
deadline = time.time() + 240
while time.time() < deadline:
    status, snapshot = api("GET", f"/api/render/{bar_id}?cursor=0&wait=10")
    if snapshot.get("status") in ("done", "error", "cancelled"):
        break
check(snapshot.get("status") == "done", f"纹理填充柱状图应完成，实际 {snapshot.get('status')}")
bar_city = (snapshot.get("result") or {}).get("cities") or []
check(bool(bar_city and bar_city[0].get("charts")),
      f"纹理填充柱状图应产出图片，实际 {bar_city[:1]}")
for path in (bar_city[0].get("files") if bar_city else []) or []:
    try:
        Path(path).unlink()
    except OSError:
        pass

# ---- 多图与对比 ----------------------------------------------------------

status, payload = api("POST", "/api/render", {
    "mode": "multi", "cities": ["237", "1", "156"], "grid": "2x2",
    "profile": "default", "values": {**FAST, "output.multi_name_template": "_apitest_multi"},
    "save_files": True,
})
multi_id = (payload.get("task") or {}).get("id")
check(status == 200 and multi_id, f"多图任务应可提交，实际 {status} {payload}")
snapshot = {}
deadline = time.time() + 240
while time.time() < deadline:
    status, snapshot = api("GET", f"/api/render/{multi_id}?cursor=0&wait=10")
    if snapshot.get("status") in ("done", "error", "cancelled"):
        break
check(snapshot.get("status") == "done", f"多图任务应完成，实际 {snapshot.get('status')}")
multi = (snapshot.get("result") or {}).get("multi") or {}
check(multi.get("grid") == "2x2", f"多图应报告画布 2x2，实际 {multi.get('grid')}")
check(any("画布 2x2" in log["message"] for log in snapshot["logs"]),
      "多图日志应说明画布")
for path in multi.get("files") or []:
    try:
        Path(path).unlink()
    except OSError:
        pass

status, payload = api("POST", "/api/render", {
    "mode": "compare", "cities": ["237", "1"], "profile": "compare",
    "values": {**FAST, "output.compare_name_template": "_apitest_compare"},
    "save_files": True,
})
compare_id = (payload.get("task") or {}).get("id")
snapshot = {}
deadline = time.time() + 240
while time.time() < deadline:
    status, snapshot = api("GET", f"/api/render/{compare_id}?cursor=0&wait=10")
    if snapshot.get("status") in ("done", "error", "cancelled"):
        break
check(snapshot.get("status") == "done", f"对比任务应完成，实际 {snapshot.get('status')}")
compare = (snapshot.get("result") or {}).get("compare") or {}
check(compare.get("series"), f"对比图应报告对比要素，实际 {compare}")
for path in compare.get("files") or []:
    try:
        Path(path).unlink()
    except OSError:
        pass

# 城市名类型统一：短名开启时，结果清单与文件名都不应再出现完整名（含「，」）
status, payload = api("POST", "/api/render", {
    "mode": "multi", "cities": ["637", "634"], "grid": "1x2", "profile": "default",
    "values": {**FAST, "data.city_short_name": True,
               "output.multi_name_template": "{cities}"},
    "save_files": True,
})
name_id = (payload.get("task") or {}).get("id")
snapshot = {}
deadline = time.time() + 240
while time.time() < deadline:
    status, snapshot = api("GET", f"/api/render/{name_id}?cursor=0&wait=10")
    if snapshot.get("status") in ("done", "error", "cancelled"):
        break
check(snapshot.get("status") == "done", f"短名多图任务应完成，实际 {snapshot.get('status')}")
named = (snapshot.get("result") or {}).get("multi") or {}
drawn_names = [item["name"] for item in named.get("drawn") or []]
check(len(drawn_names) == 2 and all("，" not in name for name in drawn_names),
      f"短名开启时结果清单应用短名，实际 {drawn_names}")
check(all("，" not in Path(path).stem for path in named.get("files") or []),
      f"短名开启时文件名不应用完整名，实际 {named.get('files')}")
for path in named.get("files") or []:
    try:
        Path(path).unlink()
    except OSError:
        pass

# 多元素对比：facet（一格一元素）与 overlay（同图叠加）
for compare_mode in ("facet", "overlay"):
    status, payload = api("POST", "/api/render", {
        "mode": "compare", "cities": ["237", "1"], "profile": "compare",
        "values": {**FAST,
                   "compare.metrics": ["minTemp", "maxTemp"],
                   "compare.grid": "1x2",
                   "compare.mode": compare_mode,
                   "output.compare_name_template": f"_apitest_compare_{compare_mode}"},
        "save_files": True,
    })
    task_id = (payload.get("task") or {}).get("id")
    snapshot = {}
    deadline = time.time() + 240
    while time.time() < deadline:
        status, snapshot = api("GET", f"/api/render/{task_id}?cursor=0&wait=10")
        if snapshot.get("status") in ("done", "error", "cancelled"):
            break
    check(snapshot.get("status") == "done",
          f"多元素对比（{compare_mode}）应完成，"
          f"实际 {snapshot.get('status')} {snapshot.get('error')}")
    multi_compare = (snapshot.get("result") or {}).get("compare") or {}
    check(len(multi_compare.get("files") or []) >= 1,
          f"多元素对比（{compare_mode}）应产出图片，实际 {multi_compare}")
    check(len(multi_compare.get("series") or []) >= 2,
          f"多元素对比（{compare_mode}）应报告 2 个元素，实际 {multi_compare.get('series')}")
    messages = [entry.get("message", "") for entry in snapshot.get("logs") or []]
    check(any("对比元素：minTemp、maxTemp" in text for text in messages),
          f"日志应报告多元素与呈现方式，实际 {messages[-6:]}")
    for path in multi_compare.get("files") or []:
        try:
            Path(path).unlink()
        except OSError:
            pass

# 全黑多城：靠线型 / 标记 / 柱填充区分城市（style_dimension=city）
status, payload = api("POST", "/api/render", {
    "mode": "compare", "cities": ["637", "634"], "profile": "compare",
    "values": {**FAST,
               "compare.colors": ["#000000"],
               "compare.metrics": [],
               "compare.metric": "rainfall",
               "compare.chart_type": "bar",
               "compare.bar_fill_cycle": ["solid", "hollow"],
               "compare.bar_hatch_cycle": ["", ""],
               "compare.linestyle_cycle": ["-", "--"],
               "compare.marker_cycle": ["o", "s"],
               "compare.style_dimension": "city",
               "output.compare_name_template": "_apitest_black"},
    "save_files": True,
})
black_id = (payload.get("task") or {}).get("id")
snapshot = {}
deadline = time.time() + 240
while time.time() < deadline:
    status, snapshot = api("GET", f"/api/render/{black_id}?cursor=0&wait=10")
    if snapshot.get("status") in ("done", "error", "cancelled"):
        break
check(snapshot.get("status") == "done",
      f"全黑多城对比应完成，实际 {snapshot.get('status')} {snapshot.get('error')}")
black_compare = (snapshot.get("result") or {}).get("compare") or {}
check(len(black_compare.get("files") or []) >= 1,
      f"全黑多城对比应产出图片，实际 {black_compare}")
for path in black_compare.get("files") or []:
    try:
        Path(path).unlink()
    except OSError:
        pass

# ---- 错误路径 ------------------------------------------------------------

status, payload = api("POST", "/api/render", {"mode": "city", "cities": [], "profile": "default"})
check(status == 400 and "城市" in payload.get("error", ""), f"缺目标应 400，实际 {payload}")

status, payload = api("POST", "/api/render", {
    "mode": "compare", "cities": ["237"], "profile": "default"})
check(status == 400, f"对比图单城市应 400，实际 {status}")

status, payload = api("POST", "/api/render", {
    "mode": "city", "cities": ["237"], "profile": "default",
    "values": {"output.chart_dpi": 0}})
check(status == 400 and payload.get("errors"), f"非法配置应 400 并给出字段错误，实际 {payload}")

status, payload = api("GET", "/api/render/deadbeef")
check(status == 404, f"未知任务应 404，实际 {status}")

status, payload = api("POST", "/api/render/88888888/artifact/chart-000/cancel")
check(status == 404, f"未知任务取消应 404，实际 {status}")

# ---- 缓存 ----------------------------------------------------------------

status, payload = api("GET", "/api/cache")
check(status == 200 and payload["items"], f"缓存统计应可用，实际 {payload}")
check(payload["files"] >= 1, "暖缓存下应统计到缓存文件")

# ---- 城市查询 ------------------------------------------------------------

status, payload = api("GET", "/api/cities?q=" + urllib.parse.quote("北京") + "&limit=5")
check(status == 200 and any("北京" in c["name"] for c in payload["cities"]),
      f"城市查询应能命中北京，实际 {status} {payload}")
check(all("cityId" in c and "coords" in c for c in payload["cities"]),
      "城市查询结果应含编号与坐标")

# 逐项解析（cityId + 城市名混填）→ 用于「指定城市颜色」的键候选
status, payload = api("GET", "/api/cities?resolve=" + urllib.parse.quote("237,北京,火星"))
check(status == 200 and "resolved" in payload, f"按清单解析应成功，实际 {status} {payload}")
items = {item["input"]: item for item in payload["resolved"]}
check(items["237"]["ok"] and items["237"]["cityId"] == 237, "数字应按 cityId 命中")
check(bool(items["237"]["display"]), "解析结果应带显示名（color_map 的键）")
check(items["北京"]["ok"] and items["北京"]["cityId"] == 237, "城市名应解析出 cityId")
check(items["火星"]["ok"] is False and items["火星"].get("error"), "不存在的城市应标记未命中")
check(all(set(item) >= {"cityId", "display", "memName"} for item in payload["resolved"]
          if item["ok"]), "命中项应含 cityId / display / memName")

# 出图接口也应做模式感知校验
status, payload = api("POST", "/api/render", {
    "mode": "compare", "cities": ["237", "1"], "profile": "compare",
    "values": {"compare.color_map": {"北京": "浅蓝"}}, "save_files": False,
})
check(status == 400 and payload.get("errors"),
      f"非法城市颜色应在出图前被拦下，实际 {status} {payload}")

# ---- 结果 ----------------------------------------------------------------

httpd.shutdown()

print(f"断言通过 {PASSED} 项，失败 {len(FAILED)} 项")
for message in FAILED:
    print(f"  [失败] {message}")
sys.exit(1 if FAILED else 0)
