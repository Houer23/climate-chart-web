"""配置服务回归测试（离线）。

覆盖：配置清单扫描、按数据类型的校验、保存修改、另存（全量 / 继承差量）三种模式，
并验证「继承差量」写出的文件确实只含差异项、且重新解析后与全量配置等价。

测试会在 ``config/custom/`` 下创建两个临时配置并在结束时清理。
运行：``python tests/test_config_service.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from app import config_service as cs  # noqa: E402
from app import state_store  # noqa: E402
from core.config_loader import load_config  # noqa: E402

FULL_NAME = "__webtest_full"
DIFF_NAME = "__webtest_diff"

PASSED = 0
FAILED: list[str] = []


def check(condition: bool, message: str) -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(message)


def cleanup() -> None:
    for name in (FULL_NAME, DIFF_NAME):
        try:
            cs.delete_profile(name)
        except cs.ConfigServiceError:
            pass


cleanup()

# ---- 1. 配置清单 ---------------------------------------------------------

profiles = cs.scan_profiles()
names = [item.name for item in profiles]
check("default" in names, "配置清单应含 default")
check("简图" in names, "配置清单应含内置自定义配置「简图」")
check("简2" in names, "配置清单应含多文件自定义配置「简2」")
check(names[0] == "default", f"default 应排在最前，实际 {names[:3]}")

by_name = {item.name: item for item in profiles}
check(by_name["简2"].source.endswith("config/custom/简2.yaml"),
      f"「简2」来源应为 config/custom/简2.yaml，实际 {by_name['简2'].source}")
check(by_name["简2"].shape == "single", "「简2」应是单配置裸文件结构")
check(by_name["简图"].shape == "wrapped", "「简图」应是 profiles 包裹结构")
check(by_name["default"].builtin and not by_name["default"].shadowed,
      "default 应为内置且未被覆盖")
check(cs.default_profile_name() == "default",
      f"默认配置名应为 default，实际 {cs.default_profile_name()}")
check("presentation" in names, "内置预设 presentation 应在清单中")

# ---- 2. 按数据类型的校验 -------------------------------------------------

bad = cs.validate_payload({"profile": "default", "values": {"data.temp_unit": "K"}})
check(not bad["ok"], "非法枚举值应校验失败")
check(bad["errors"] and bad["errors"][0]["path"] == "data.temp_unit",
      f"错误应定位到 data.temp_unit，实际 {bad['errors']}")

bad2 = cs.validate_payload({"profile": "default",
                            "values": {"figure.figsize": [12, 6, 3], "output.chart_dpi": 0}})
check(len(bad2["errors"]) == 2, f"应报出 2 个字段错误，实际 {bad2['errors']}")

unknown = cs.validate_payload({"profile": "default", "values": {"figure.ttile.show": True}})
check(not unknown["ok"] and unknown["errors"][0]["path"] == "figure.ttile.show",
      "未知配置项应被拒绝")

good = cs.validate_payload({"profile": "default", "values": {
    "multi.grid": "2 × 2", "data.temp_unit": "F", "figure.facecolor": "#112233",
}})
check(good["ok"], f"合法值应通过校验：{good['errors']}")
check(good["normalized"]["multi.grid"] == "2x2",
      f"multi.grid 应被归一为 2x2，实际 {good['normalized']['multi.grid']}")
check(good["normalized"]["figure.facecolor"] == "#112233", "颜色应原样保留")

warn = cs.validate_payload({"profile": "default", "values": {
    "series.minTemp.enabled": False, "series.maxTemp.enabled": False,
    "series.rainfall.enabled": False,
}})
check(not warn["ok"] and any("没有启用任何绘图元素" in e["message"] for e in warn["errors"]),
      f"全部元素关闭应触发跨字段错误，实际 {warn['errors']}")

extra = cs.validate_payload({"profile": "default", "values": {"axes_x.label_text": "月份"}})
check(extra["ok"], "扩展键 axes_x.label_text 应被接受")
check(not any("不是已知项" in w["message"] for w in extra["warnings"]),
      f"扩展键不应报未知项告警，实际 {extra['warnings']}")

fill_warn = cs.validate_payload({"profile": "default", "values": {
    "series.minTemp.chart_type": "fill_between",
    "series.minTemp.fill_between_key": "min",
}})
check(any("fill_between_key" in w["message"] for w in fill_warn["warnings"]),
      f"fill_between 的非法边界键应给出告警，实际 {fill_warn['warnings']}")

# ---- 2.5 模式感知：compare.color_map 只在多城市对比时校验 -----------------

COLOR_MAP = {"北京": "#d85a30", "火星": "#185fa5"}

# 非对比模式（单城 / 多图）：完全忽略 color_map，不出任何相关提示
for other_mode in ("city", "multi", ""):
    quiet = cs.validate_payload({"profile": "default", "mode": other_mode,
                                 "values": {"compare.color_map": COLOR_MAP},
                                 "compare_cities": ["北京", "香港"]})
    check(quiet["ok"], f"mode={other_mode or '未指定'} 时 color_map 不应阻断校验")
    check(not any("color_map" in w["message"] for w in quiet["warnings"]),
          f"mode={other_mode or '未指定'} 时不应有任何 color_map 提示，实际 {quiet['warnings']}")

# 对比模式：键不在本次对比城市中 → 告警（但不阻断）
compare_warn = cs.validate_payload({
    "profile": "default", "mode": "compare",
    "values": {"compare.color_map": COLOR_MAP},
    "compare_cities": ["北京", "香港"],
})
check(compare_warn["ok"], "color_map 键不在城市集合内只应是告警、不阻断")
hits = [w for w in compare_warn["warnings"] if w["path"] == "compare.color_map"]
check(len(hits) == 1, f"应只对未命中的「火星」报 1 条告警，实际 {hits}")
check("火星" in hits[0]["message"] and "北京" in hits[0]["message"],
      f"告警应点名未命中键与当前城市，实际 {hits[0]['message']}")

# 对比模式：全部命中 → 无告警
clean = cs.validate_payload({
    "profile": "default", "mode": "compare",
    "values": {"compare.color_map": {"北京": "#d85a30", "香港": "#185fa5"}},
    "compare_cities": ["北京", "香港"],
})
check(not any("color_map" in w["message"] for w in clean["warnings"]),
      f"全部命中时不应有提示，实际 {clean['warnings']}")

# 对比模式：拿不到城市清单 → 保持沉默（避免误报）
silent = cs.validate_payload({
    "profile": "default", "mode": "compare",
    "values": {"compare.color_map": COLOR_MAP},
})
check(not any("color_map" in w["message"] for w in silent["warnings"]),
      f"没有城市清单时不应臆测，实际 {silent['warnings']}")

# 对比模式 + color_by=city_id → color_map 不生效，必须提示
shadowed = cs.validate_payload({
    "profile": "default", "mode": "compare",
    "values": {"compare.color_map": {"北京": "#d85a30"}, "compare.color_by": "city_id"},
    "compare_cities": ["北京", "香港"],
})
check(any("city_id" in w["message"] and w["path"] == "compare.color_map"
          for w in shadowed["warnings"]),
      f"color_by=city_id 时应提示 color_map 不生效，实际 {shadowed['warnings']}")

# 非对比模式即使 color_by=city_id 也不提示
noop = cs.validate_payload({
    "profile": "default", "mode": "city",
    "values": {"compare.color_map": {"北京": "#d85a30"}, "compare.color_by": "city_id"},
})
check(not any("color_map" in w["message"] for w in noop["warnings"]),
      f"非对比模式不应有 color_map 提示，实际 {noop['warnings']}")

# 空 color_map 不产生任何提示
empty_map = cs.validate_payload({"profile": "default", "mode": "compare",
                                 "values": {"compare.color_map": {}},
                                 "compare_cities": ["北京"]})
check(not any("color_map" in w["message"] for w in empty_map["warnings"]),
      "空 color_map 不应有提示")

# ---- 3. 另存：全量 -------------------------------------------------------

VALUES = {
    "figure.figsize": [13.0, 7.0],
    "data.temp_unit": "F",
    "series.meanTemp.enabled": True,
    "series.meanTemp.color": "#123456",
    "axes_primary.limit": [-20, 40],
}

saved = cs.save_profile({
    "name": FULL_NAME, "mode": "full", "base": "default",
    "description": "回归测试：全量", "values": VALUES,
})
full_cfg = load_config(FULL_NAME)
check(full_cfg["figure"]["figsize"] == [13.0, 7.0], "全量保存后画布尺寸应生效")
check(full_cfg["data"]["temp_unit"] == "F", "全量保存后温度单位应生效")
check(full_cfg["series"]["meanTemp"]["enabled"] is True, "全量保存后元素开关应生效")
check(full_cfg["profile_description"] == "回归测试：全量", "全量保存后说明应生效")
check(full_cfg["output"]["table_formats"] == ["csv", "md", "xlsx"],
      "全量保存应写入被改过的项")

full_file = cs.profile_file(FULL_NAME)
full_raw = yaml.safe_load(full_file.read_text(encoding="utf-8"))
check("extends" not in full_raw, "全量保存的文件不应含 extends")
check("figure" in full_raw and "series" in full_raw, "全量保存的文件应含改动所在的分组")
check(saved["mode"] == "full", f"返回的 mode 应为 full，实际 {saved['mode']}")

# ---- 4. 另存：继承差量 ---------------------------------------------------

diff_saved = cs.save_profile({
    "name": DIFF_NAME, "mode": "extends", "base": "default",
    "description": "回归测试：差量", "values": VALUES,
})
diff_file = cs.profile_file(DIFF_NAME)
diff_raw = yaml.safe_load(diff_file.read_text(encoding="utf-8"))
check(diff_raw.get("extends") == "default",
      f"差量文件应 extends 当前配置，实际 {diff_raw.get('extends')}")
check(diff_saved["mode"] == "extends", "返回的 mode 应为 extends")

diff_cfg = load_config(DIFF_NAME)
check(diff_cfg["figure"]["figsize"] == [13.0, 7.0], "差量配置解析后画布尺寸应一致")
check(diff_cfg["data"]["temp_unit"] == "F", "差量配置解析后温度单位应一致")
check(diff_cfg["series"]["meanTemp"]["enabled"] is True, "差量配置解析后元素开关应一致")
check(diff_cfg["axes_primary"]["limit"] == [-20, 40], "差量配置解析后量程应一致")

# 差量文件必须**只含差异项**
check(set(diff_raw) <= {"description", "extends", "figure", "data", "series", "axes_primary"},
      f"差量文件顶层键应只有差异项，实际 {sorted(diff_raw)}")
check(list(diff_raw.get("figure", {})) == ["figsize"],
      f"figure 下应只写 figsize，实际 {sorted(diff_raw.get('figure', {}))}")
check(list(diff_raw.get("series", {})) == ["meanTemp"],
      f"series 下应只写 meanTemp，实际 {sorted(diff_raw.get('series', {}))}")
check(diff_raw.get("series", {}).get("meanTemp", {}) == {"enabled": True, "color": "#123456"},
      f"meanTemp 下应只写改动项，实际 {diff_raw.get('series', {}).get('meanTemp')}")
check("output" not in diff_raw and "figure" in diff_raw and len(diff_raw["figure"]) == 1,
      "差量文件不应包含未改动的分组")

# 差量洁净度：运行时的 profile_description、以及「基线没有、新值为空」的扩展键
# （axes_x.label_text 被规范化成 ""、compare.color_map 为 {}）都不应写进文件
check("profile_description" not in diff_raw,
      f"差量不应写入 profile_description（core 加载时会丢弃它），实际 {sorted(diff_raw)}")
check("axes_x" not in diff_raw,
      f"基线里不存在的空扩展键不应写进差量，实际 axes_x={diff_raw.get('axes_x')!r}")
check("compare" not in diff_raw,
      f"空的 color_map 不应写进差量，实际 compare={diff_raw.get('compare')!r}")

# 全量另存只保留「与默认值不同」的项：一项都不改的探针应当只剩说明
probe_full = cs.build_save_payload("__probe__", "full", {}, base="default")
check({k for k in probe_full if k != "description"} == set(),
      f"全量另存应剔除等于默认值的项，实际仍剩 "
      f"{sorted(k for k in probe_full if k != 'description')}")
check(full_file.stat().st_size > 0 and diff_file.stat().st_size > 0,
      "全量与差量文件都不应为空文件")

# 等价性：差异项之外，两者应完全一致
def flatten(node, prefix=""):
    out = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(flatten(value, f"{prefix}.{key}" if prefix else key))
    else:
        out[prefix] = node
    return out


flat_full = {k: v for k, v in flatten(full_cfg).items()
             if not k.startswith("_") and not k.startswith("profile_")}
flat_diff = {k: v for k, v in flatten(diff_cfg).items()
             if not k.startswith("_") and not k.startswith("profile_")}
differing = {k for k in set(flat_full) | set(flat_diff)
             if flat_full.get(k) != flat_diff.get(k)}
check(not differing, f"全量与差量配置应完全等价，差异：{sorted(differing)[:8]}")

# ---- 4.5 全配置回环 -------------------------------------------------------
# 表单装载的是「解析后的全部字段值」，用户不改动就点出图时，这些值必须原样通过校验。
# 这一步能一次性暴露「表单读出的类型与后端要求不一致」的问题。

FIELD_COUNT = len(cs.schema.field_index())
for item in cs.scan_profiles():
    detail = cs.profile_detail(item.name)
    checked = cs.validate_payload({"profile": item.name, "values": detail["values"]})
    check(not checked["errors"],
          f"「{item.name}」回环校验不应出现字段级错误，实际 {checked['errors'][:3]}")
    check(len(detail["values"]) == FIELD_COUNT,
          f"「{item.name}」表单值应覆盖全部 {FIELD_COUNT} 个字段，"
          f"实际 {len(detail['values'])}")
    check(not detail["unknownKeys"],
          f"「{item.name}」不应含表单外的未知键，实际 {detail['unknownKeys']}")

# ---- 4.6 说明文案来源与「全量」洁净度 ------------------------------------

from_values = cs.build_save_payload("__probe__", "extends",
                                    {**VALUES, "profile_description": "来自表单的说明"},
                                    base="default")
check(from_values.get("description") == "来自表单的说明",
      f"未显式给 description 时应回退到表单的 profile_description，"
      f"实际 {from_values.get('description')!r}")
check("profile_description" not in from_values,
      "profile_description 不应被写进配置体")

explicit = cs.build_save_payload("__probe__", "extends", VALUES,
                                 base="default", description="显式说明")
check(explicit.get("description") == "显式说明", "显式传入的 description 应优先")

full_payload = cs.build_save_payload("__probe__", "full", {}, base="default")
check("profile_description" not in full_payload and "profile_name" not in full_payload,
      f"全量配置不应含运行时键，实际 {sorted(full_payload)[:6]}")
check("label_text" not in full_payload.get("axes_x", {}),
      "全量配置应剔除空的扩展键 axes_x.label_text")
check("color_map" not in full_payload.get("compare", {}),
      "全量配置应剔除空的扩展键 compare.color_map")
check({k for k in full_payload if k != "description"} == set(),
      f"一项都不改时，全量另存应只剩说明（默认值全部剔除），实际 {sorted(full_payload)}")

tuned = cs.build_save_payload("__probe__", "full",
                              {"axes_x.label_fontsize": 15, "figure.title.text": "{city} 自定义"},
                              base="default")
check(tuned.get("axes_x") == {"label_fontsize": 15.0},
      f"只改一项时，该组应只剩这一项（等于默认值的兄弟项被剔除），"
      f"实际 {tuned.get('axes_x')!r}")
check(tuned.get("figure", {}).get("title") == {"text": "{city} 自定义"},
      f"全量配置应保留非默认的取值，实际 {tuned.get('figure', {}).get('title')!r}")

# 存一份带说明的差量后再加载：说明文案应更新，且不含 profile_description
cs.save_profile({"name": DIFF_NAME, "mode": "keep", "base": DIFF_NAME,
                 "values": {**VALUES, "profile_description": "改过的说明"}})
diff_text = cs.profile_file(DIFF_NAME).read_text(encoding="utf-8")
check("profile_description" not in diff_text,
      f"配置文件里不应出现 profile_description，实际：{diff_text}")
check("description: 改过的说明" in diff_text, "配置文件里应写入 description")
check(load_config(DIFF_NAME)["profile_description"] == "改过的说明",
      "重新加载后说明文案应更新")

# ---- 4.7 保存修改应保留「原本显式写过」的项 ------------------------------
# 「保存修改」此前只写与继承值不同的项，会把原本显式固化的项悄悄变成"跟随继承"。
# 现在：原本写过的项即使与继承值相同也继续保留；顺带清掉空值噪音、提示注释丢失。

PIN_NAME = "__webtest_pin"
PIN_FILE = cs.SAVE_DIR / f"{PIN_NAME}.yaml"
PIN_FILE.write_text(
    "# 这是用户自己写的注释\n"
    "extends: default\n"
    "description: pin 测试\n"
    "axes_primary:\n"
    "  label_rotation: 90\n"      # 与 DEFAULTS 相同 → 属于「冗余显式项」
    "  tick_fontsize: 10\n"
    "figure:\n"
    "  legend: {title: ''}\n",     # 空但有意为之的值（不该被当噪音删掉）
    encoding="utf-8",
)
try:
    pin_result = cs.save_profile({
        "name": PIN_NAME, "mode": "keep", "base": PIN_NAME,
        "values": cs.profile_detail(PIN_NAME)["values"],
    })
    pin_text = PIN_FILE.read_text(encoding="utf-8")
    pin_raw = yaml.safe_load(pin_text)
    check(pin_raw.get("axes_primary", {}).get("label_rotation") == 90,
          f"冗余的显式项 label_rotation 应保留，实际 {pin_raw.get('axes_primary')}")
    check(pin_raw.get("axes_primary", {}).get("tick_fontsize") == 10,
          "冗余的显式项 tick_fontsize 应保留")
    check(pin_raw.get("figure", {}).get("legend", {}).get("title") == "",
          "有意义的空字符串（图例标题）应保留")
    check(pin_raw.get("extends") == "default", "extends 应保留")
    check("profile_description" not in pin_raw and "compare" not in pin_raw
          and "axes_x" not in pin_raw,
          f"运行时键与空扩展键不应写入，实际 {sorted(pin_raw)}")
    check(pin_result["lostComments"] == 1,
          f"应报告原文件有 1 行用户注释会丢失，实际 {pin_result['lostComments']}")
    check("# 这是用户自己写的注释" not in pin_text, "（已知取舍）回写不保留用户注释")
    check(cs.profile_file(PIN_NAME) is not None, "pin 测试文件应存在")
finally:
    try:
        cs.delete_profile(PIN_NAME)
    except cs.ConfigServiceError:
        pass
check(cs.profile_file(PIN_NAME) is None, "pin 测试文件应已清理")

# 另存为「继承差量」不带 keep_explicit：与继承值相同、原本没写过的项不写入
fresh = cs.build_save_payload("__probe__", "extends", {}, base="default")
check("axes_primary" not in fresh,
      f"全新差量不应写入与基线相同的项，实际 {sorted(fresh)}")

# ---- 4.8 上次使用的配置（界面状态持久化） --------------------------------

state_store.reset()
check(state_store.last_profile() == "", "初始应没有「上次使用的配置」")

state_store.remember_profile("简2")
check(state_store.last_profile() == "简2", "应记住配置名")
check(bool(state_store.read_state()["updated_at"]), "应记录更新时间")
check(state_store.state_path().is_file(), "状态文件应已生成")
check(state_store.read_state()["last_profile"] == "简2", "重新读取应一致")

state_store.remember_profile("默认配置不存在也不要紧")
check(state_store.last_profile() == "默认配置不存在也不要紧",
      "状态只存名字、不校验存在性（由前端回退）")
state_store.remember_profile(None)
check(state_store.last_profile() == "", "传 None 应清除记录")

# 损坏的状态文件必须按「没有状态」处理，绝不影响主流程
state_store.remember_profile("简2")
state_store.state_path().write_text("{ 这不是 JSON", encoding="utf-8")
check(state_store.last_profile() == "", "损坏的状态文件应被当作无状态")
state_store.remember_profile("default")
check(state_store.last_profile() == "default", "损坏后仍应能重新写入")

# 界面状态不是数据缓存，不应计入缓存占用
stats_before = cs.cache_stats()["files"]
state_store.remember_profile("简2")
stats_after = cs.cache_stats()["files"]
check(state_store.state_path().is_file(), "状态文件应确实存在（否则上面的对比没意义）")
check(stats_before == stats_after, "状态文件不应计入缓存统计")
state_store.reset()

# 初始配置选择规则（服务端算好给前端）：上次使用且仍在 → 默认 → 清单第一项
check(cs.pick_profile("简2", ["default", "简2"], "default") == "简2",
      "应选上次使用的配置")
check(cs.pick_profile("已被删除", ["default", "简2"], "default") == "default",
      "上次使用的配置不存在时应回退默认")
check(cs.pick_profile("", ["default", "简2"], "default") == "default",
      "从未记录过时应选默认配置")
check(cs.pick_profile("  简2  ", ["简2"], "default") == "简2",
      "应忽略配置名首尾空白")
check(cs.pick_profile("x", ["简2"], "default") == "简2",
      "默认配置不在清单里时应取清单第一项")
check(cs.pick_profile("x", [], "default") == "default", "清单为空时应兜底 default")

real_names = cs.profile_names()
check(cs.pick_profile("简2", real_names, "default") == "简2", "真实清单里应能选中 简2")
check(cs.pick_profile("不存在的配置", real_names, "default") == "default",
      "真实清单里应回退默认")
check(cs.pick_profile("", real_names, cs.default_profile_name()) == "default",
      "与真实默认配置联动应一致")

# 左右分栏比例（拖动分隔条后的宽度）
state_store.reset()
check(state_store.left_ratio() == 0.0, "初始应没有分栏比例")
state_store.remember_ratio(0.42)
check(abs(state_store.left_ratio() - 0.42) < 1e-9, "应记住分栏比例")
state_store.remember_ratio("坏值")
check(state_store.left_ratio() == 0.0, "非数字应被当作未设置")
state_store.remember_ratio(float("nan"))
check(state_store.left_ratio() == 0.0, "NaN 应被当作未设置")
state_store.remember_ratio(0)
check(state_store.left_ratio() == 0.0, "0 表示未设置（由前端用默认值）")
state_store.remember_ratio(1.5)
check(state_store.left_ratio() == 0.0, "越界比例应被丢弃（夹紧由前端按窗口做）")

# 两个字段互不覆盖（写一个不该清掉另一个）
state_store.remember_profile("简2")
check(state_store.left_ratio() == 0.0 and state_store.last_profile() == "简2",
      "写配置名不应影响比例字段")
state_store.remember_ratio(0.4)
check(state_store.last_profile() == "简2", "写比例也不应清掉配置名")
state_store.reset()
check(state_store.left_ratio() == 0.0 and state_store.last_profile() == "",
      "重置后应全部清空")

# ---- 5. 保存修改（keep）：保持差量结构 -----------------------------------

cs.save_profile({"name": DIFF_NAME, "mode": "keep", "base": DIFF_NAME,
                 "description": "回归测试：差量（改）",
                 "values": {**VALUES, "figure.figsize": [14.0, 6.5]}})
kept_raw = yaml.safe_load(cs.profile_file(DIFF_NAME).read_text(encoding="utf-8"))
check(kept_raw.get("extends") == "default", "保存修改应保持原 extends 结构")
check(kept_raw["figure"]["figsize"] == [14.0, 6.5], "保存修改应写入新值")
check(load_config(DIFF_NAME)["figure"]["figsize"] == [14.0, 6.5], "保存修改后应可解析")
check(load_config(DIFF_NAME)["profile_description"] == "回归测试：差量（改）",
      "保存修改应更新说明")

# 内置配置同名保存 = 只写自定义文件覆盖，内置文件本身不动
builtin_before = cs.DEFAULT_PROFILES_PATH.read_bytes()
cs.save_profile({"name": "compact", "mode": "full", "base": "compact",
                 "values": {"figure.figsize": [6.0, 6.0]}})
check(cs.DEFAULT_PROFILES_PATH.read_bytes() == builtin_before,
      "保存同名配置不得改动内置 profiles.yaml")
check(load_config("compact")["figure"]["figsize"] == [6.0, 6.0],
      "同名保存后自定义版本应生效（覆盖内置同名配置）")
check(cs.profile_file("compact").name == "compact.yaml",
      "同名保存应写入 config/custom/compact.yaml")
shadow = {item.name: item for item in cs.scan_profiles()}["compact"]
check(shadow.builtin and shadow.shadowed, "同名配置在清单中应标记为「内置 + 被覆盖」")
check("config/custom/compact.yaml" in shadow.source,
      f"同名配置的来源应指向自定义文件，实际 {shadow.source}")

# ---- 6. 导出 -------------------------------------------------------------

check("figure:" in cs.template_text("default"), "全量模板应含 figure 分组")
final_text = cs.final_config_text("default")
check("_available_profiles" not in final_text and "profile_name:" in final_text,
      "最终配置导出应剔除内部键、保留配置标识（与 --show-config 一致）")
check("temp_unit: F" in cs.final_config_text("default", {"data.temp_unit": "F"}),
      "最终配置导出应反映表单值")

# ---- 7. 名称与删除 -------------------------------------------------------

for bad_name in ("", "  ", ".hidden", "a/b", 'a:b'):
    try:
        cs.save_profile({"name": bad_name, "mode": "full", "values": {}})
        check(False, f"非法配置名 {bad_name!r} 应被拒绝")
    except cs.ConfigServiceError:
        check(True, "")

try:
    cs.delete_profile("default")
    check(False, "内置配置不应允许删除")
except cs.ConfigServiceError:
    check(True, "")

# ---- 清理与结果 ----------------------------------------------------------

cleanup()
cs.delete_profile("compact")   # 上面同名保存产生的自定义文件
check(cs.profile_file("compact") is None, "清理后不应残留 compact.yaml")
check(cs.profile_file(FULL_NAME) is None, "清理后不应残留临时全量配置")
check(cs.profile_file(DIFF_NAME) is None, "清理后不应残留临时差量配置")
check((cs.profile_file("简2") is not None), "既有自定义配置 简2.yaml 不应被测试破坏")

print(f"断言通过 {PASSED} 项，失败 {len(FAILED)} 项")
for message in FAILED:
    print(f"  [失败] {message}")
sys.exit(1 if FAILED else 0)
