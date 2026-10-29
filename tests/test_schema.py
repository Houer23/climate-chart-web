"""配置 Schema 自检（离线，可反复运行）。

校验三件事：
1. **完整性**：Schema 覆盖 ``core/config_loader.DEFAULTS`` 的每一个叶子路径，不多不少
   （扩展键 ``extra=True`` 允许额外存在）。
2. **自洽性**：每一项的**默认值**都能通过它自己声明的类型与范围校验
   （默认值都不合法，说明 Schema 写错了）。
3. **严格性**：每一类字段的典型非法输入都能被准确拦截。

运行：``python tests/test_schema.py``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import schema  # noqa: E402
from core.config_loader import DEFAULTS  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(condition: bool, message: str) -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(message)


# ---- 1. 完整性 -----------------------------------------------------------

LEAF_DICT_KINDS = {"str_map", "json"}
INDEX = schema.field_index()


def default_leaf_paths(node: Any, prefix: str = "") -> list[str]:
    """取 ``DEFAULTS`` 的叶子路径；``str_map`` / ``json`` 类型的字典算叶子。"""
    leaves: list[str] = []
    if isinstance(node, dict):
        field = INDEX.get(prefix)
        if prefix and field is not None and field.kind in LEAF_DICT_KINDS:
            return [prefix]
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            leaves.extend(default_leaf_paths(value, path))
        return leaves
    return [prefix]


default_paths = set(default_leaf_paths(DEFAULTS))
schema_paths = set(INDEX)
extra_paths = schema.extra_paths()

missing = sorted(default_paths - schema_paths)
unknown = sorted(schema_paths - default_paths - extra_paths)

check(not missing, f"Schema 缺少以下配置项：{missing}")
check(not unknown, f"Schema 多出以下配置项（既不在 DEFAULTS 也不是扩展键）：{unknown}")
check(len(extra_paths) == 2, f"扩展键应为 2 个，实际 {sorted(extra_paths)}")

# ---- 2. 自洽性：默认值必须合法 -------------------------------------------

for field in schema.all_fields():
    if field.extra:
        continue
    path = field.path
    value: Any = DEFAULTS
    for part in path.split("."):
        value = value[part]
    errors = schema.validate_value(field, value)
    check(not errors, f"默认值不合法 {path}={value!r}：{errors}")

# ---- 3. 严格性：典型非法输入必须被拦截 -----------------------------------

def field_of(path: str) -> schema.Field:
    return INDEX[path]


def rejects(path: str, raw: Any) -> bool:
    return bool(schema.validate_value(field_of(path), raw))


def accepts(path: str, raw: Any) -> bool:
    return not schema.validate_value(field_of(path), raw)


check(rejects("data.temp_unit", "K"), "data.temp_unit 应拒绝 K")
check(accepts("data.temp_unit", "F"), "data.temp_unit 应接受 F")

check(rejects("output.chart_dpi", 0), "output.chart_dpi 应拒绝 0（必须 ≥ 1）")
check(rejects("output.chart_dpi", 12.5), "output.chart_dpi 应拒绝小数")
check(accepts("output.chart_dpi", "300"), "output.chart_dpi 应接受数字字符串 300")

check(rejects("data.coord.decimals", -1), "data.coord.decimals 应拒绝负数")
check(rejects("data.derive_mean_temp", "yes"), "布尔字段应拒绝 yes（YAML 陷阱）")
check(accepts("data.derive_mean_temp", "false"), "布尔字段应接受 'false'")

check(rejects("figure.facecolor", "浅蓝"), "颜色字段应拒绝中文色名")
check(accepts("figure.axes_facecolor", "auto"), "颜色字段应接受 auto")
check(rejects("figure.axes_facecolor", "inherit"), "颜色字段应拒绝未声明的特殊值")

check(rejects("figure.figsize", [12, 6, 3]), "figure.figsize 应拒绝 3 个元素")
check(rejects("figure.figsize", "12,6"), "figure.figsize 应拒绝字符串")
check(accepts("figure.figsize", ["12", 6]), "figure.figsize 应接受数字字符串")

check(rejects("axes_primary.limit", [1]), "axes_primary.limit 应拒绝单个数值")
check(accepts("axes_primary.limit", []), "axes_primary.limit 应接受空数组（自动量程）")

check(rejects("figure.title.text", "{unknown}"), "模板字段应拒绝未知占位符")
check(rejects("figure.title.text", "{city} · {humidity}"), "模板字段应拒绝混入的未知占位符")
check(accepts("figure.title.text", "{city} 气候 ({period})"), "模板字段应接受已知占位符")
check(accepts("figure.title.text", "{city!r} {city_id:>4}"), "模板字段应接受格式说明与转换")
check(rejects("fetch.data_path", "{lang}/{cityid}.xml"), "路径模板应拒绝未知占位符")
check(accepts("fetch.data_path", "{lang}/json/{city_id}_{lang}.xml"), "路径模板应接受已知占位符")

check(rejects("output.chart_formats", ["tiff"]), "图片格式应拒绝 tiff")
check(accepts("output.chart_formats", ["png", "svg"]), "图片格式应接受 png/svg")
check(rejects("table.rows", ["humidity"]), "表格行应拒绝未知元素")
check(accepts("output.table_formats", []), "表格格式应允许空数组（不出表格）")

check(rejects("multi.grid", "2by2"), "multi.grid 应拒绝非法排列写法")
check(rejects("multi.grid", "2x0"), "multi.grid 应拒绝 0 行/列")
check(accepts("multi.grid", "auto"), "multi.grid 应接受 auto")
check(accepts("multi.grid", " 3 × 2 "), "multi.grid 应接受「3 × 2」并归一为 3x2")
check(schema.normalize_value(field_of("multi.grid"), "2×2") == "2x2",
      "multi.grid 应把全角 × 归一为 x")
check(rejects("multi.legend", "each"), "multi.legend 应拒绝未声明取值")
check(accepts("multi.figsize", None), "multi.figsize 应接受 null")

check(rejects("axes_primary.tick_count", 2.5), "int_or_null 应拒绝小数")
check(accepts("axes_primary.tick_step", None), "num_or_null 应接受 null")
check(accepts("axes_primary.label_rotation", "vertical"), "rotation 应接受 vertical")
check(rejects("axes_primary.label_rotation", "sideways"), "rotation 应拒绝非法文本")

check(rejects("compare.colors", ["红色"]), "颜色数组应拒绝非法色名")
check(rejects("fetch.headers", ["a"]), "str_map 应拒绝数组")
check(accepts("fetch.headers", {"Referer": "https://x/"}), "str_map 应接受对象")

check(rejects("series.minTemp.alpha", 1.5), "透明度应拒绝 > 1")
check(rejects("series.minTemp.marker", "q"), "标记应拒绝未声明符号")
check(accepts("series.minTemp.marker", ""), "标记应接受空字符串（不显示）")
check(accepts("series.minTemp.marker", "."), "标记应接受小点 .（matplotlib 合法符号）")

check(rejects("data.lang", {"a": 1}), "文本字段应拒绝对象")

# ---- 3.5 多元素对比（metrics / mode / facet / legend / element_styles） ----

check(accepts("compare.metrics", ["minTemp", "rainfall"]), "多元素对比应接受元素数组")
check(schema.normalize_value(field_of("compare.metrics"), ["minTemp", "minTemp", "rainfall"])
      == ["minTemp", "rainfall"], "多元素应保序去重")
check(rejects("compare.metrics", ["minTemp", "maxTemp", "meanTemp", "rainfall", "raindays"]),
      "多元素对比最多 4 个")
check(rejects("compare.metrics", "minTemp,maxTemp"), "多元素必须是数组，逗号串不行")
check(rejects("compare.metrics", ["humidity"]), "应拒绝未知元素")
check(field_of("compare.metrics").max_items == 4, "compare.metrics 应声明 maxItems=4")
check(accepts("compare.metrics", []), "空数组表示沿用单元素 compare.metric")

check(accepts("compare.mode", "facet") and accepts("compare.mode", "overlay"),
      "呈现方式应接受 facet / overlay")
check(rejects("compare.mode", "grid"), "呈现方式应拒绝未声明取值")
check(accepts("compare.grid", "auto") and accepts("compare.grid", "2x2"),
      "分面排版应接受 auto 与 列x行")
check(rejects("compare.grid", "2by2"), "分面排版应拒绝非法写法")
check(accepts("compare.share_ylim", "row") and rejects("compare.share_ylim", "col"),
      "分面纵轴量程只接受 row / none")

check(accepts("compare.sort_metric", "") and accepts("compare.sort_metric", "rainfall"),
      "排序基准应接受空值与已知元素")
check(rejects("compare.sort_metric", "rain"), "排序基准应拒绝未知元素")
check(schema.empty_value(field_of("compare.sort_metric")) == "", "排序基准的空值应为空串")

check(accepts("compare.legend.position", "bottom"), "图例位置应接受 bottom")
check(rejects("compare.legend.position", "left"), "图例位置应拒绝未声明取值")
check(accepts("compare.legend.layout", "metric"), "图例排列应接受 metric")
check(rejects("compare.legend.layout", "row"), "图例排列应拒绝未声明取值")
check(accepts("compare.facet_figsize", None) and accepts("compare.facet_figsize", [14, 8]),
      "分面画布应接受 null 与 [宽, 高]")
check(accepts("compare.hspace", None) and rejects("compare.hspace", "auto"),
      "分面间距应接受 null、拒绝文本")

check(accepts("compare.element_styles.minTemp.chart_type", None),
      "元素样式的图形类型应接受 null（跟随对比图形类型）")
check(accepts("compare.element_styles.minTemp.chart_type", "bar"),
      "元素样式的图形类型应接受合法取值")
check(rejects("compare.element_styles.minTemp.chart_type", "curve"),
      "元素样式的图形类型应拒绝未声明取值")
check(schema.normalize_value(field_of("compare.element_styles.minTemp.chart_type"), "")
      is None, "空串应归一为 null（跟随）")
check(schema.empty_value(field_of("compare.element_styles.minTemp.chart_type")) is None,
      "enum_or_null 的空值应为 null")
check(accepts("compare.element_styles.rainfall.linestyle", ":")
      and rejects("compare.element_styles.rainfall.linestyle", "dash"),
      "元素样式的线型只接受声明过的线型")
check(accepts("compare.element_styles.rainfall.marker", "v")
      and rejects("compare.element_styles.rainfall.marker", "q"),
      "元素样式的标记只接受合法符号")

check(accepts("compare.title_text_multi", "{metrics} 对比 · {city}（{metric_count} 项）"),
      "多元素标题应接受 {metrics} / {metric_count}")
check(rejects("compare.title_text_multi", "{elements} 对比"), "多元素标题应拒绝未知占位符")
check(accepts("output.compare_name_template", "{city_count}城_{metrics}_{mode}"),
      "对比文件名模板应接受 {metrics} / {mode}")
check("metrics" in schema.COMPARE_ONLY_PLACEHOLDERS
      and "mode" in schema.COMPARE_ONLY_PLACEHOLDERS,
      "多元素占位符应列入「仅对比模式可用」白名单")

# ---- 4. 映射型字段的语义元信息与空值 -------------------------------------

color_map_field = field_of("compare.color_map")
check(color_map_field.kind == "str_map", "compare.color_map 应是映射类型")
check(color_map_field.key_kind == "city", "compare.color_map 的键应声明为 city（城市显示名）")
check(color_map_field.value_kind == "color", "compare.color_map 的值应声明为 color")
check(len(color_map_field.value_options) >= 8,
      f"compare.color_map 应提供 ≥8 个预设颜色，实际 {len(color_map_field.value_options)}")
check(all(re.match(r"^#[0-9a-fA-F]{6}$", str(value))
          for value, _label in color_map_field.value_options),
      "预设颜色应全部是 6 位 HEX")
check(any("默认第 1 色" in label for _value, label in color_map_field.value_options),
      "预设颜色应标出与 compare.colors 默认序列的对应关系")

check(accepts("compare.color_map", {"北京": "#d85a30", "香港": "#185fa5"}),
      "应接受合法的「城市 → 颜色」")
check(accepts("compare.color_map", {"北京": ""}), "应接受空颜色（表示未设置）")
check(rejects("compare.color_map", {"北京": "浅蓝"}), "应拒绝非法颜色值")
check(rejects("compare.color_map", {"北京": "none"}),
      "城市颜色应是真实颜色，不接受 none（该城市线条会不可见）")
check(rejects("compare.color_map", ["a"]), "应拒绝数组")
check(schema.normalize_value(color_map_field, {" 北京 ": " #D85A30 "}) == {" 北京 ": "#D85A30"},
      "应保留键原样、清理颜色两侧空白")

check(schema.empty_value(field_of("compare.color_map")) == {}, "映射型空值应为 {}")
check(schema.empty_value(field_of("axes_x.label_text")) == "", "文本型空值应为空串")
check(schema.empty_value(field_of("axes_primary.limit")) == [], "可空 num2 的空值应为 []")
check(schema.empty_value(field_of("multi.figsize")) is None, "可空 num2_or_null 的空值应为 null")
check(schema.empty_value(field_of("series.minTemp.color")) == "", "颜色型空值应为空串")
check(schema.empty_value(field_of("output.table_formats")) == [], "数组型空值应为 []")

# 未知项告警过滤：扩展键不应再报「请核对拼写」
raw_warnings = [
    "以下配置项不是已知项，已被忽略（请核对拼写）：axes_x.label_text, compare.color_map",
    "有元素使用副轴（axis=secondary），但 axes_secondary.show=false，副轴元素将不被绘制",
]
filtered = schema.filter_unknown_warnings(raw_warnings)
check(len(filtered) == 1, f"扩展键告警应被剔除，实际保留 {filtered}")
check("副轴" in filtered[0], "非扩展键告警应原样保留")

mixed = schema.filter_unknown_warnings(
    ["以下配置项不是已知项，已被忽略（请核对拼写）：axes_x.label_text, figure.ttile.show"]
)
check(len(mixed) == 1 and "figure.ttile.show" in mixed[0],
      f"应只保留真正的未知键，实际 {mixed}")

# ---- 结果 ----------------------------------------------------------------

print(f"配置项总数：{len(INDEX)}（其中扩展键 {len(extra_paths)}）")
print(f"断言通过 {PASSED} 项，失败 {len(FAILED)} 项")
for message in FAILED:
    print(f"  [失败] {message}")
sys.exit(1 if FAILED else 0)
