"""配置 Schema：把 ``core/config_loader.DEFAULTS`` 的每一个配置项补上

* **数据类型**（bool / int / float / 枚举 / 颜色 / 模板 / 数组 / 映射 / JSON）
* **取值范围**（min / max / step）
* **候选值**（枚举项及其中文说明）
* **说明文案**（取自参考项目的 ``config/README.md`` 配置字典）
* **分组**（A~J，与配置字典章节一一对应）

网页配置表单、前端即时校验、后端字段级校验全部由本文件驱动，
从而做到「配置项按照配置参数数据类型进行输入验证」。

两点关于参考项目的小适配（**不修改 core**）
--------------------------------------------
``core`` 里有两个键被绘图代码实际读取、但**未列入** ``DEFAULTS``：
``axes_x.label_text`` 与 ``compare.color_map``。它们在本 Schema 中标记为
``extra=True``，可以被表单编辑；对应的「未知配置项」告警会在
:func:`filter_unknown_warnings` 中被精确剔除，不影响其余告警。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Iterable, Optional

# ---- 复用的枚举 ----------------------------------------------------------

SERIES_KEYS: tuple[str, ...] = ("maxTemp", "minTemp", "meanTemp", "rainfall", "raindays")

SERIES_LABELS: dict[str, str] = {
    "maxTemp": "日均最高气温",
    "minTemp": "日均最低气温",
    "meanTemp": "日均气温",
    "rainfall": "降水量",
    "raindays": "降水日数",
}

#: 归属轴（``series.axis`` 与 ``figure.mean_rain_line.axis`` 共用前三项）
AXIS_ENUM: tuple[tuple[str, str], ...] = (
    ("primary", "主轴（温度）"),
    ("secondary", "副轴（降水）"),
    ("tertiary", "第三轴（降水日数）"),
)

SERIES_ENUM: tuple[tuple[str, str], ...] = tuple(
    (key, f"{label}（{key}）") for key, label in SERIES_LABELS.items()
)

CHART_TYPE_ENUM: tuple[tuple[str, str], ...] = (
    ("smooth", "平滑曲线"),
    ("line", "折线"),
    ("bar", "柱状"),
    ("area", "面积"),
    ("step", "阶梯"),
    ("scatter", "散点"),
    ("fill_between", "区间带"),
)

LINESTYLE_ENUM: tuple[tuple[str, str], ...] = (
    ("-", "实线 -"),
    ("--", "虚线 --"),
    ("-.", "点划线 -."),
    (":", "点线 :"),
)

MARKER_ENUM: tuple[tuple[str, str], ...] = (
    ("", "不显示"),
    (".", "小点 ."),
    ("o", "圆点 o"),
    ("s", "方块 s"),
    ("^", "上三角 ^"),
    ("v", "下三角 v"),
    ("D", "菱形 D"),
    ("P", "加号 P"),
    ("X", "叉号 X"),
    ("*", "星形 *"),
    ("+", "十字 +"),
    ("x", "小叉 x"),
)

TICK_DIRECTION_ENUM: tuple[tuple[str, str], ...] = (
    ("out", "向外 out"),
    ("in", "向内 in"),
    ("inout", "双向 inout"),
)

SCALE_ENUM: tuple[tuple[str, str], ...] = (("linear", "线性"), ("log", "对数"))

LABEL_POSITION_ENUM: tuple[tuple[str, str], ...] = (
    ("auto", "自动"),
    ("bottom", "沿轴底部"),
    ("center", "沿轴居中"),
    ("top", "沿轴顶部"),
    ("left", "偏左"),
    ("right", "偏右"),
)

LABEL_ALIGN_ENUM: tuple[tuple[str, str], ...] = (
    ("auto", "自动"),
    ("left", "左对齐"),
    ("center", "居中"),
    ("right", "右对齐"),
)

LABEL_VALIGN_ENUM: tuple[tuple[str, str], ...] = (
    ("auto", "自动"),
    ("top", "顶部对齐"),
    ("center", "垂直居中"),
    ("bottom", "底部对齐"),
    ("baseline", "基线对齐"),
    ("center_baseline", "居中基线"),
)

LOC_ENUM: tuple[tuple[str, str], ...] = (
    ("left", "左"),
    ("center", "中"),
    ("right", "右"),
)

DATA_LABEL_POSITION_ENUM: tuple[tuple[str, str], ...] = (
    ("top", "数据点上方"),
    ("bottom", "数据点下方"),
    ("center", "数据点居中"),
)

#: 模板占位符（绘图文本上下文，``core/chart.py::_context``）；
#: ``metric`` / ``city_count`` 仅对比图 / 多图可用（见 ``_multi_context``）
TEXT_PLACEHOLDERS: tuple[str, ...] = (
    "city", "city_id", "member", "org", "station", "period",
    "lat", "lon", "temp_unit", "rain_unit", "profile",
    "metric", "city_count",
)
#: 仅对比图 / 多图可用的占位符（单城出图时会出现 KeyError）
COMPARE_ONLY_PLACEHOLDERS: tuple[str, ...] = ("metric", "metrics", "mode", "city_count")

#: 预设颜色（键值表里「颜色」下拉的候选）：前 8 个就是 ``compare.colors`` 的默认序列，
#: 其余为官方页面配色与常用色。
COLOR_PRESETS: tuple[tuple[str, str], ...] = (
    ("#d85a30", "1 砖红（默认第 1 色）"),
    ("#185fa5", "2 靛蓝（默认第 2 色）"),
    ("#3b6d11", "3 草绿（默认第 3 色）"),
    ("#993556", "4 紫红（默认第 4 色）"),
    ("#854f0b", "5 赭石（默认第 5 色）"),
    ("#0f6e56", "6 墨绿（默认第 6 色）"),
    ("#534ab7", "7 蓝紫（默认第 7 色）"),
    ("#a32d2d", "8 深红（默认第 8 色）"),
    ("#eb6877", "官方最高气温粉红"),
    ("#0f91c4", "官方最低气温蓝"),
    ("#46cbd4", "官方降水青"),
    ("#990000", "官方平均气温暗红"),
    ("#2c3e50", "深蓝灰（标题色）"),
    ("#5f666e", "中灰（刻度色）"),
    ("#e69138", "橙"),
    ("#6aa84f", "草绿"),
    ("#7f7f7f", "中灰"),
    ("#000000", "黑"),
)
#: 文件名模板占位符（``core/pipeline.py::_format_basename``）
FILE_PLACEHOLDERS: tuple[str, ...] = (
    "city", "city_id", "member", "station", "profile", "lat", "lon",
)


# ---- 数据结构 ------------------------------------------------------------

@dataclass(frozen=True)
class Field:
    """一个可配置项的类型与约束描述。"""

    path: str
    kind: str
    label: str
    desc: str = ""
    enum: tuple[tuple[Any, str], ...] = ()
    options: tuple[tuple[Any, str], ...] = ()
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    unit: str = ""
    placeholder: str = ""
    suggestions: tuple[str, ...] = ()
    placeholders: tuple[str, ...] = ()
    specials: tuple[str, ...] = ()
    text_options: tuple[str, ...] = ()
    coerce: str = ""
    allow_empty: bool = True
    extra: bool = False
    readonly: bool = False
    #: ``list_enum`` 最多可选几项（0 = 不限）
    max_items: int = 0
    #: 所属子分组键（``series.<元素>.*`` / ``compare.element_styles.<元素>.*``）
    subgroup: str = ""
    #: ``enum_or_null`` 里「空值」选项的文案
    null_label: str = "（自动）"
    #: 映射类型的语义：键是什么（``city`` = 城市显示名，候选由城市上下文提供）
    key_kind: str = ""
    #: 映射类型的语义：值是什么（``color`` = 颜色，候选见 ``value_options``）
    value_kind: str = ""
    #: 值下拉候选（``((值, 说明), ...)``）
    value_options: tuple[tuple[Any, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "label": self.label,
            "desc": self.desc,
            "enum": [{"value": v, "label": t} for v, t in self.enum],
            "options": [{"value": v, "label": t} for v, t in self.options],
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "unit": self.unit,
            "placeholder": self.placeholder,
            "suggestions": list(self.suggestions),
            "placeholders": list(self.placeholders),
            "specials": list(self.specials),
            "textOptions": list(self.text_options),
            "allowEmpty": self.allow_empty,
            "extra": self.extra,
            "readonly": self.readonly,
            "maxItems": self.max_items,
            "subgroup": self.subgroup,
            "nullLabel": self.null_label,
            "keyKind": self.key_kind,
            "valueKind": self.value_kind,
            "valueOptions": [{"value": v, "label": t} for v, t in self.value_options],
        }


@dataclass(frozen=True)
class Group:
    """表单分组（与 ``config/README.md`` 的 A~J 章节对应）。"""

    key: str
    title: str
    desc: str
    fields: tuple[Field, ...]
    series: bool = False       # True = 该组的字段按 series 元素展开
    axes: bool = False         # True = 该组是纵轴（字段来自 AXES_FIELDS）
    prefix: str = ""           # 字段写的是相对路径时，用它补前缀
    #: 二级子分组 ``(键, 标题)``；字段用 ``Field.subgroup`` 归属，界面渲染成二级折叠块
    subgroups: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "desc": self.desc,
            "series": self.series,
            "axes": self.axes,
            "subgroups": [{"value": k, "label": v} for k, v in self.subgroups],
            "fields": [f.to_dict() for f in group_fields(self)],
        }


def group_fields(group: "Group") -> tuple[Field, ...]:
    """把分组里的字段展开为带完整点路径的字段列表。"""
    if group.series:
        return tuple(f for key in SERIES_KEYS for f in series_fields(key))
    if group.axes:
        return axes_fields(group.key)
    if group.prefix:
        return tuple(
            Field(**{**f.__dict__, "path": f"{group.prefix}.{f.path}"}) for f in group.fields
        )
    return group.fields


class FieldError(ValueError):
    """某个配置项的取值不符合其数据类型/范围。"""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}：{message}")
        self.path = path
        self.message = message


# ---- 字段构造助手 --------------------------------------------------------

def _f(path: str, kind: str, label: str, desc: str = "", **kw: Any) -> Field:
    return Field(path=path, kind=kind, label=label, desc=desc, **kw)


def B(path: str, label: str, desc: str = "") -> Field:
    return _f(path, "bool", label, desc)


def I(path: str, label: str, desc: str = "", lo: Optional[float] = None,
      hi: Optional[float] = None, unit: str = "") -> Field:
    return _f(path, "int", label, desc, min=lo, max=hi, step=1, unit=unit)


def FL(path: str, label: str, desc: str = "", lo: Optional[float] = None,
       hi: Optional[float] = None, step: Optional[float] = None,
       unit: str = "") -> Field:
    return _f(path, "float", label, desc, min=lo, max=hi, step=step, unit=unit)


def E(path: str, label: str, enum: tuple[tuple[Any, str], ...], desc: str = "",
      coerce: str = "str") -> Field:
    return _f(path, "enum", label, desc, enum=enum, coerce=coerce)


def S(path: str, label: str, desc: str = "", suggestions: Iterable[str] = ()) -> Field:
    return _f(path, "str", label, desc, suggestions=tuple(suggestions))


def T(path: str, label: str, placeholders: tuple[str, ...], desc: str = "") -> Field:
    return _f(path, "template", label, desc, placeholders=placeholders)


def P(path: str, label: str, desc: str = "") -> Field:
    return _f(path, "path", label, desc)


def C(path: str, label: str, desc: str = "",
      specials: tuple[str, ...] = ("auto", "none")) -> Field:
    return _f(path, "color", label, desc, specials=specials)


# =========================================================================
# A. data —— 数据与字段口径
# =========================================================================

DATA_FIELDS: tuple[Field, ...] = (
    S("data.lang", "语言版本",
      "决定数据源 URL 路径与月份标签语种。必须是 WMO 站点支持的语言代码。",
      suggestions=("zh", "en", "fr", "es", "ru", "ar", "de", "pt", "ja", "ko")),
    B("data.derive_mean_temp", "派生平均气温",
      "数据源的 meanTemp 恒为空，是否用 (日均最高 + 日均最低) / 2 派生。"),
    E("data.temp_unit", "温度单位", (("C", "摄氏度 °C"), ("F", "华氏度 °F"))),
    E("data.rain_unit", "降水单位", (("mm", "毫米 mm"), ("inch", "英寸 inch"))),
    E("data.fill_missing", "缺失值处理",
      (("gap", "断线（缺测月不画）"), ("zero", "补 0"), ("skip", "跳过该点"))),
    E("data.month_label_style", "月份标签样式",
      (("1月", "1月"), ("一月", "一月"), ("Jan", "Jan"), ("01", "01"))),
    B("data.include_annual", "附加年值",
      "表格与图表是否附加年值（温度取月均值，降水取累加）。"),
    B("data.auto_disable_empty_series", "空元素自动不画",
      "某元素在该城市全无数据时自动跳过（如纯气温城市自动去掉降水柱）。"),
    B("data.city_short_name", "城市名取短名",
      "取逗号前第一段（如「洛杉矶，加利福尼亚州」→「洛杉矶」）。"),
    E("data.coord.style", "经纬度显示方式",
      (("direction", "带方向符号（S/W 区分南北西东）"), ("signed", "纯数字（西经/南纬为负数）"))),
    E("data.coord.direction", "方向符号形式",
      (("letter", "字母 E/W/N/S"), ("hanzi", "汉字 东/西/南/北"))),
    B("data.coord.unit", "经纬度带单位", "是否在经纬度后附加单位符号（默认 °）。"),
    S("data.coord.unit_text", "经纬度单位文案", "默认符号 °，可改为「度」。",
      suggestions=("°", "度")),
    I("data.coord.decimals", "经纬度小数位", "经纬度保留的小数位（≥ 0）。", lo=0, hi=8),
)

# =========================================================================
# B. fetch —— 请求层
# =========================================================================

FETCH_FIELDS: tuple[Field, ...] = (
    S("fetch.base_url", "站点根地址", "数据源站点根，便于换成镜像。"),
    T("fetch.data_path", "气候数据路径模板", ("lang", "city_id"),
      "内容实为 JSON；也是唯一的取数地址，每城 1 次请求。"),
    T("fetch.city_index_path", "城市索引路径模板", ("lang",),
      "用于城市名反查 cityId。"),
    FL("fetch.timeout", "请求超时", "作用于 socket 连接与读写。", lo=0.1, step=1, unit="秒"),
    FL("fetch.resolve_timeout", "DNS 预解析超时",
      "urllib 的 timeout 不覆盖 getaddrinfo；这里单独兜一层。0 = 关闭预检查。",
      lo=0, step=1, unit="秒"),
    I("fetch.retries", "重试次数", "应对数据源实测的间歇性 TLS 断连。", lo=0, hi=20),
    FL("fetch.backoff", "退避基数", "第 n 次重试等待 backoff × 2^(n-1) 秒。",
       lo=0, step=0.1, unit="秒"),
    FL("fetch.backoff_max", "退避上限", "单次退避的最长等待。", lo=0, step=1, unit="秒"),
    FL("fetch.min_interval", "请求最小间隔",
      "相邻网络请求的排队限速（含重试），批量出图时对数据源保持礼貌。0 = 不限速。",
      lo=0, step=0.1, unit="秒"),
    B("fetch.verify_ssl", "校验证书", "不建议关闭；代理模式下同样生效。"),
    S("fetch.proxy", "代理地址", "如 http://127.0.0.1:7890；留空 = 直连。"),
    B("fetch.use_env_proxy", "沿用环境变量代理",
      "仅在「代理地址」为空时生效；默认关闭，避免环境里的失效代理把请求带去死地址。"),
    B("fetch.save_raw", "保存原始响应", "把数据源原始响应落到「原始响应目录」，便于排查。"),
    P("fetch.raw_dir", "原始响应目录", "相对路径基于项目根目录解析。"),
    _f("fetch.headers", "str_map", "主请求头",
       "逐项可配；可追加任意自定义头（如 Referer / Cookie），与默认头合并。",
       suggestions=("User-Agent", "Accept", "Accept-Language", "Accept-Encoding",
                    "Connection", "Cache-Control", "Pragma",
                    "Upgrade-Insecure-Requests", "Referer", "Cookie")),
    B("fetch.cache.enabled", "启用本地缓存", "命中缓存不发起网络请求，也不占限速额度。"),
    FL("fetch.cache.ttl", "缓存有效期", "超过该时长的缓存视为过期。",
       lo=0, step=60, unit="秒"),
    P("fetch.cache.dir", "缓存目录", "HTTP 响应缓存与城市索引缓存的存放目录。"),
)

# =========================================================================
# C. output —— 输出与文件
# =========================================================================

OUTPUT_FIELDS: tuple[Field, ...] = (
    P("output.out_dir", "输出目录", "相对路径基于项目根目录解析。"),
    T("output.name_template", "图片文件名模板",
      FILE_PLACEHOLDERS, "不含扩展名；默认带 {profile} 后缀以便区分配置。"),
    T("output.table_name_template", "表格文件名模板",
      FILE_PLACEHOLDERS, "默认不带配置名后缀。"),
    T("output.compare_name_template", "对比图文件名模板",
      ("city_count", "metric", "metrics", "mode", "cities", "profile"),
      "多元素时 {metric} 会自动展开成全部元素（如 minTemp-rainfall），"
      "overlay 再加 _overlay 后缀；{metrics} 恒为全部元素用 - 连接，{mode} = facet / overlay。"),
    T("output.multi_name_template", "多图文件名模板",
      ("city_count", "cities", "grid", "profile"), "含 {grid} 可避免不同排列互相覆盖。"),
    _f("output.table_formats", "list_enum", "表格格式",
       "空数组 = 不输出表格。",
       options=(("csv", "CSV"), ("md", "Markdown"), ("xlsx", "Excel"), ("json", "JSON"))),
    _f("output.chart_formats", "list_enum", "图片格式",
       "空数组 = 不出图。svg / pdf 为矢量格式。",
       options=(("png", "PNG"), ("svg", "SVG"), ("pdf", "PDF"),
                ("jpg", "JPG"), ("webp", "WebP"))),
    I("output.chart_dpi", "位图 DPI", "svg / pdf 为矢量格式，不受影响。", lo=1, hi=1200),
    E("output.overwrite", "覆盖策略",
      (("overwrite", "覆盖"), ("skip", "跳过已存在"), ("timestamp", "加时间戳"))),
    S("output.csv_encoding", "CSV 编码", "默认带 BOM，便于 Excel 直接打开不乱码。",
      suggestions=("utf-8-sig", "utf-8", "gbk", "gb18030")),
    E("output.log_level", "日志级别",
      (("DEBUG", "DEBUG"), ("INFO", "INFO"), ("WARNING", "WARNING"), ("ERROR", "ERROR"))),
)

# =========================================================================
# D. figure —— 画布、标题、图例、网格、背景
# =========================================================================

FIGURE_FIELDS: tuple[Field, ...] = (
    _f("figure.figsize", "num2", "画布尺寸", "单位：英寸 [宽, 高]。"),
    I("figure.dpi", "画布分辨率", "画布 DPI；实际出图 DPI 以「位图 DPI」优先。", lo=1, hi=1200),
    C("figure.facecolor", "画布底色",
      "支持 8 位 HEX（含透明度），如 #ffffff80；none = 透明。",
      specials=("none",)),
    C("figure.axes_facecolor", "绘图区底色",
      "auto = 由画布底色向白混合派生；none = 透明。", specials=("auto", "none")),
    C("figure.edgecolor", "画布边框", "", specials=("none",)),
    E("figure.layout", "布局引擎",
      (("tight", "tight（紧凑）"), ("constrained", "constrained（约束）"), ("none", "none（不调整）"))),
    _f("figure.subplots_adjust.left", "num_or_null", "手动左边距", "非 null 才生效。"),
    _f("figure.subplots_adjust.right", "num_or_null", "手动右边距", "非 null 才生效。"),
    _f("figure.subplots_adjust.top", "num_or_null", "手动上边距", "非 null 才生效。"),
    _f("figure.subplots_adjust.bottom", "num_or_null", "手动下边距", "非 null 才生效。"),
    _f("figure.subplots_adjust.wspace", "num_or_null", "子图列间距", "非 null 才生效。"),
    _f("figure.subplots_adjust.hspace", "num_or_null", "子图行间距", "非 null 才生效。"),
    _f("figure.font_family", "list_str", "字体族",
      "按序回退，防中文方框。",
      suggestions=("Microsoft YaHei", "SimHei", "SimSun", "DejaVu Sans", "Arial")),
    FL("figure.font_size", "全局基准字号", "", lo=1, step=0.5),
    S("figure.font_weight", "全局字重", "",
      suggestions=("normal", "bold", "light", "medium", "semibold", "heavy", "black")),
    B("figure.axes_unicode_minus", "负号用真减号", "避免负号显示为短横。"),
    E("figure.svg_fonttype", "SVG 文字形态",
      (("none", "保留为可编辑文字（<text>）"), ("path", "转轮廓路径（不可编辑）"))),
    E("figure.pdf_fonttype", "PDF 文字形态",
      ((42, "42 - TrueType 内嵌（可选中/可编辑）"), (3, "3 - Type 3")), coerce="int"),
    B("figure.title.show", "显示标题", ""),
    T("figure.title.text", "标题文本", TEXT_PLACEHOLDERS),
    FL("figure.title.fontsize", "标题字号", "", lo=1, step=0.5),
    C("figure.title.color", "标题颜色", "", specials=()),
    S("figure.title.fontweight", "标题字重", "",
      suggestions=("normal", "bold", "light", "medium", "semibold", "heavy", "black")),
    FL("figure.title.pad", "标题间距", "", step=1),
    E("figure.title.loc", "标题位置", LOC_ENUM),
    B("figure.subtitle.show", "显示副标题", ""),
    T("figure.subtitle.text", "副标题文本", TEXT_PLACEHOLDERS),
    FL("figure.subtitle.fontsize", "副标题字号", "", lo=1, step=0.5),
    C("figure.subtitle.color", "副标题颜色", "", specials=()),
    S("figure.subtitle.fontweight", "副标题字重", "",
      suggestions=("normal", "bold", "light", "medium", "semibold", "heavy", "black")),
    FL("figure.subtitle.pad", "副标题间距", "", step=1),
    E("figure.subtitle.loc", "副标题位置", LOC_ENUM),
    B("figure.legend.show", "显示图例", ""),
    S("figure.legend.loc", "图例位置", "matplotlib 的 loc 字符串。",
      suggestions=("best", "upper right", "upper left", "lower right", "lower left",
                   "upper center", "lower center", "center left", "center right", "center")),
    _f("figure.legend.bbox_to_anchor", "num2_or_null", "图例锚点",
       "画布外置需给负值，如 [0.5, -0.12]；null = 由 loc 决定。"),
    I("figure.legend.ncol", "图例列数", "0 = 按元素数量自动。", lo=0, hi=20),
    FL("figure.legend.fontsize", "图例字号", "", lo=1, step=0.5),
    B("figure.legend.frameon", "图例边框", ""),
    FL("figure.legend.framealpha", "图例边框透明度", "", lo=0, hi=1, step=0.05),
    C("figure.legend.facecolor", "图例底色", "", specials=()),
    C("figure.legend.edgecolor", "图例边框色", "", specials=("none",)),
    S("figure.legend.title", "图例标题", "留空则不显示。"),
    FL("figure.legend.markerscale", "标记缩放", "", lo=0, step=0.1),
    FL("figure.legend.columnspacing", "图例列间距", "", lo=0, step=0.1),
    FL("figure.legend.handlelength", "图例线长", "", lo=0, step=0.1),
    FL("figure.legend.handletextpad", "图例文字间距", "", lo=0, step=0.1),
    B("figure.grid.show", "显示网格", ""),
    E("figure.grid.axis", "网格方向",
      (("y", "仅水平线"), ("x", "仅垂直线"), ("both", "两者都有"))),
    E("figure.grid.which", "网格刻度",
      (("major", "主刻度"), ("minor", "次刻度"), ("both", "两者"))),
    C("figure.grid.color", "网格颜色", "", specials=()),
    S("figure.grid.linestyle", "网格线型", "", suggestions=("-", "--", "-.", ":")),
    FL("figure.grid.linewidth", "网格线宽", "", lo=0, step=0.1),
    FL("figure.grid.alpha", "网格透明度", "", lo=0, hi=1, step=0.05),
    FL("figure.grid.zorder", "网格层级", "", step=1),
    B("figure.background.bands.show", "显示背景色带",
      "画在最底层，始终位于柱状图之下，不会罩住降水柱。"),
    E("figure.background.bands.mode", "色带模式",
      (("season", "季节色带"), ("alternate", "隔月交替"))),
    E("figure.background.bands.hemisphere", "半球",
      (("auto", "按城市纬度自动判定"), ("north", "北半球"), ("south", "南半球"))),
    C("figure.background.bands.alternate_color", "交替色带颜色", "", specials=()),
    FL("figure.background.bands.alternate_alpha", "交替色带透明度", "",
       lo=0, hi=1, step=0.01),
    FL("figure.background.bands.alpha", "季节色带透明度", "", lo=0, hi=1, step=0.01),
    _f("figure.background.bands.seasons", "json", "季节定义",
       "每项 {name, months:[...], color}；按北半球惯例声明，南半球由「半球」自动平移半年。"),
    B("figure.zeroline.show", "显示 0 基准线", ""),
    C("figure.zeroline.color", "基准线颜色", "", specials=()),
    S("figure.zeroline.linestyle", "基准线线型", "", suggestions=("-", "--", "-.", ":")),
    FL("figure.zeroline.linewidth", "基准线线宽", "", lo=0, step=0.1),
    FL("figure.zeroline.alpha", "基准线透明度", "", lo=0, hi=1, step=0.05),
    B("figure.mean_rain_line.show", "显示平均降水线",
      "12 个月降水量的平均水平线；取值只看该城市有无降水数据，与降水元素是否绘制无关。"),
    E("figure.mean_rain_line.axis", "平均降水线所属轴",
      (("auto", "自动（跟随降水元素）"),) + AXIS_ENUM),
    C("figure.mean_rain_line.color", "平均降水线颜色", "auto = 跟随降水元素颜色。",
      specials=("auto",)),
    S("figure.mean_rain_line.linestyle", "平均降水线线型", "",
      suggestions=("--", "-", "-.", ":")),
    FL("figure.mean_rain_line.linewidth", "平均降水线线宽", "", lo=0, step=0.1),
    FL("figure.mean_rain_line.alpha", "平均降水线透明度", "", lo=0, hi=1, step=0.05),
    FL("figure.mean_rain_line.zorder", "平均降水线层级", "默认高于降水柱，不会被盖住。",
       step=1),
    S("figure.mean_rain_line.label", "图例文案", "空字符串 = 不进图例。"),
    B("figure.mean_rain_line.annotate", "标注平均值", ""),
    T("figure.mean_rain_line.annotate_template", "标注文本模板",
      ("label", "value", "unit", "city", "station")),
    C("figure.mean_rain_line.annotate_color", "标注文字颜色", "auto = 沿用线色。",
      specials=("auto",)),
    E("figure.mean_rain_line.annotate_position", "标注横向锚点",
      (("left", "绘图区左边缘"), ("center", "绘图区中线"),
       ("right", "绘图区右边缘"), ("ticks", "贴该轴刻度标签列"))),
    E("figure.mean_rain_line.annotate_side", "标注与线的关系",
      (("above", "线之上"), ("below", "线之下"), ("center", "垂直居中线"))),
    _f("figure.mean_rain_line.annotate_offset", "num2", "标注点偏移",
       "dx 向右为正；dy 取非负，方向由「标注与线的关系」决定。"),
    FL("figure.mean_rain_line.annotate_fontsize", "标注字号", "", lo=1, step=0.5),
    B("figure.annotation.show_extremes", "标注极值月", ""),
    E("figure.annotation.series", "极值标注元素", SERIES_ENUM),
    FL("figure.annotation.fontsize", "极值标注字号", "", lo=1, step=0.5),
    C("figure.annotation.color", "极值标注颜色", "", specials=()),
    B("figure.annotation.show_value", "极值标注带数值", ""),
    B("figure.annotation.avoid_overlap", "自动避让",
      "在候选位置里挑「冲突最少 → 越界最少 → 位移最小」的位置。关闭则用固定偏移。"),
    FL("figure.annotation.gap", "碰撞安全间隙", "贴得比它更近也算撞上。",
       lo=0, step=0.5, unit="点"),
    FL("figure.annotation.max_distance", "搜索半径上限", "最终偏移的横纵分量都不超过它。",
       lo=0, step=1, unit="点"),
    B("figure.annotation.allow_flip", "允许翻到另一侧",
      "关闭则最高始终在上、最低始终在下。"),
    B("figure.credit.show", "显示数据来源署名", ""),
    T("figure.credit.text", "署名文本", TEXT_PLACEHOLDERS),
    FL("figure.credit.fontsize", "署名字号", "", lo=1, step=0.5),
    C("figure.credit.color", "署名颜色", "", specials=()),
    E("figure.credit.loc", "署名位置", LOC_ENUM),
    FL("figure.credit.pad", "署名间距", "", step=1),
)

# =========================================================================
# E. axes_primary / axes_secondary / axes_tertiary —— 三个纵轴
# =========================================================================

AXES_FIELDS: tuple[Field, ...] = (
    B("show", "启用该轴", "关闭后归属该轴的元素自动跳过。"),
    E("side", "刻度侧", (("left", "左侧"), ("right", "右侧"))),
    I("offset_points", "相对右轴再外移", "仅第三轴有效。", lo=0, hi=400, unit="点"),
    S("label_text", "纵轴标题", "留空则不显示。"),
    E("label_position", "标题位置", LABEL_POSITION_ENUM),
    _f("label_x", "num_or_null", "标题横坐标", "轴坐标系比例，优先于「标题位置」。"),
    _f("label_y", "num_or_null", "标题纵坐标", "轴坐标系比例，优先于「标题位置」。"),
    FL("label_fontsize", "标题字号", "", lo=1, step=0.5),
    C("label_color", "标题颜色", "", specials=()),
    S("label_fontweight", "标题字重", "",
      suggestions=("normal", "bold", "light", "medium", "semibold", "heavy", "black")),
    _f("label_rotation", "rotation", "标题旋转角度", "角度值，或 vertical / horizontal。",
       text_options=("vertical", "horizontal")),
    E("label_align", "标题水平对齐", LABEL_ALIGN_ENUM),
    E("label_valign", "标题垂直基准", LABEL_VALIGN_ENUM),
    FL("label_pad", "标题与轴间距", "", step=0.5, unit="点"),
    _f("limit", "num2", "量程 [最小, 最大]", "空数组 = 按数据自动。"),
    _f("limit_shift", "num_or_null", "量程整体平移", "上下限同向平移，正数向上。"),
    FL("auto_pad_ratio", "自动量程留白比例", "", lo=0, hi=1, step=0.01),
    _f("tick_start", "num_or_null", "刻度起始值", ""),
    _f("tick_step", "num_or_null", "刻度间隔", "设定后按固定步长生成刻度。"),
    _f("tick_count", "int_or_null", "刻度数量", "与「刻度间隔」二选一，步长优先。"),
    _f("tick_round_to", "num_or_null", "量程取整倍数", ""),
    S("tick_format", "刻度格式串", "如 {:.0f}、{:.1f}、{:,.0f}。",
      suggestions=("{:.0f}", "{:.1f}", "{:,.0f}", "{:.2f}")),
    FL("tick_fontsize", "刻度字号", "", lo=1, step=0.5),
    C("tick_color", "刻度颜色", "", specials=()),
    FL("tick_length", "刻度长度", "", lo=0, step=0.5, unit="点"),
    FL("tick_width", "刻度线宽", "", lo=0, step=0.1),
    E("tick_direction", "刻度方向", TICK_DIRECTION_ENUM),
    E("scale", "轴刻度类型", SCALE_ENUM),
    B("spine_show", "显示轴脊", ""),
    C("spine_color", "轴脊颜色", "", specials=()),
    FL("spine_linewidth", "轴脊线宽", "", lo=0, step=0.1),
    B("hide_top_spine", "隐藏顶边轴脊", ""),
)

# =========================================================================
# F. axes_x —— 横轴（月份轴）
# =========================================================================

AXES_X_FIELDS: tuple[Field, ...] = (
    B("show", "显示横轴标题", ""),
    _f("label_text", "str", "横轴标题",
       "留空则不显示。（该键由绘图代码读取，但未列入内置默认值。）", extra=True),
    FL("label_fontsize", "标题字号", "", lo=1, step=0.5),
    C("label_color", "标题颜色", "", specials=()),
    S("label_fontweight", "标题字重", "",
      suggestions=("normal", "bold", "light", "medium", "semibold", "heavy", "black")),
    FL("label_pad", "标题与轴间距", "", step=0.5, unit="点"),
    FL("tick_rotation", "月份标签旋转", "单位：度。", step=5),
    FL("tick_fontsize", "标签字号", "", lo=1, step=0.5),
    C("tick_color", "标签颜色", "", specials=()),
    I("tick_interval", "标签间隔", "隔 N 个月显示一个标签（拥挤时用 2 或 3）。", lo=1, hi=12),
    FL("tick_length", "刻度长度", "", lo=0, step=0.5, unit="点"),
    FL("tick_width", "刻度线宽", "", lo=0, step=0.1),
    E("tick_direction", "刻度方向", TICK_DIRECTION_ENUM),
    B("show_month_gridline", "月份竖分隔线", ""),
    C("grid_color", "竖线颜色", "", specials=()),
    FL("grid_alpha", "竖线透明度", "", lo=0, hi=1, step=0.05),
    B("show_spine", "显示底边轴脊", ""),
    C("spine_color", "轴脊颜色", "", specials=()),
    FL("spine_linewidth", "轴脊线宽", "", lo=0, step=0.1),
    FL("limit_pad", "左右留白", "单位：月。", lo=0, step=0.1),
)

# =========================================================================
# G. series —— 绘图元素（每个元素独立配置）
# =========================================================================

SERIES_FIELDS: tuple[Field, ...] = (
    B("enabled", "是否绘制", ""),
    S("label", "图例名", "auto = 按元素自动命名（降水名随数据源 raintype 变化）。",
      suggestions=("auto",)),
    E("chart_type", "图形类型", CHART_TYPE_ENUM),
    E("axis", "归属轴", AXIS_ENUM),
    FL("scale_factor", "数值缩放", "把量级不同的元素压到同一轴，如降水日数 ×12。", step=0.1),
    C("color", "配色", "HEX 或颜色名。", specials=("auto",)),
    FL("alpha", "透明度", "", lo=0, hi=1, step=0.05),
    FL("linewidth", "线宽", "", lo=0, step=0.1),
    E("linestyle", "线型", LINESTYLE_ENUM),
    FL("zorder", "绘制层级", "绘制顺序固定为「先柱状、后折线」，不受此项影响。", step=1),
    E("marker", "数据点标记", MARKER_ENUM),
    FL("markersize", "标记大小", "", lo=0, step=0.5),
    C("markerfacecolor", "标记填充色", "auto = 与配色一致。", specials=("auto",)),
    C("markeredgecolor", "标记边色", "auto = 与配色一致。", specials=("auto",)),
    FL("markeredgewidth", "标记边宽", "", lo=0, step=0.1),
    I("markevery", "标记间隔", "每隔 N 个点显示一个标记。", lo=1, hi=12),
    FL("bar_width", "柱宽", "仅柱状图有效。", lo=0, hi=1, step=0.05),
    E("bar_fill", "柱填充方式",
      (("solid", "实心（纯色填充）"), ("hollow", "空心（仅边框）"), ("hatch", "纹理填充")),
      "仅柱状图有效。空心时边框色留空则取系列色；纹理填充的纹理色与边框同色。"),
    S("bar_hatch", "柱纹理样式",
      "matplotlib hatch：/ \\ | - + x o O . *；重复字符可加密（如 ///）；留空 = /。"
      "纹理填充时生效；实心下填写则在纯色上叠加纹理。",
      suggestions=("/", "//", "///", "\\\\", "|", "-", "+", "x", "o", "O", ".", "*")),
    C("bar_edgecolor", "柱边线色", "", specials=("none", "auto")),
    FL("bar_linewidth", "柱边线宽", "", lo=0, step=0.1),
    E("bar_align", "柱对齐", (("center", "居中"), ("edge", "靠边"))),
    I("smooth_points", "平滑插值点数", "仅平滑曲线有效。", lo=8, hi=2000),
    FL("fill_alpha", "面积/区间带透明度", "", lo=0, hi=1, step=0.02),
    _f("fill_between_key", "str", "区间带另一边界",
       "另一条边界元素（如 maxTemp 以 minTemp 为底）；仅区间带图形有效。",
       suggestions=SERIES_KEYS),
    B("data_labels.show", "显示数值标签", ""),
    FL("data_labels.fontsize", "数值标签字号", "", lo=1, step=0.5),
    C("data_labels.color", "数值标签颜色", "auto = 与配色一致。", specials=("auto",)),
    S("data_labels.format", "数值标签格式", "Python 格式串，如 {:.1f}。",
      suggestions=("{:.1f}", "{:.0f}", "{:.2f}")),
    FL("data_labels.offset", "数值标签偏移", "", step=1, unit="点"),
    FL("data_labels.rotation", "数值标签旋转", "单位：度。", step=5),
    E("data_labels.position", "数值标签位置", DATA_LABEL_POSITION_ENUM),
    FL("order_in_legend", "图例顺序", "小者在前。", step=1),
)

# =========================================================================
# H. table —— 表格
# =========================================================================

TABLE_FIELDS: tuple[Field, ...] = (
    _f("table.rows", "list_enum", "表格行（顺序即显示顺序）",
       "勾选要输出的行；空数组 = 不输出数据行。"
       "网页端按候选项顺序输出，自定义排序请直接编辑配置文件。", options=SERIES_ENUM),
    _f("table.row_labels", "str_map", "行名文案覆盖",
       "rainfall 用 auto 表示随数据源 raintype 自动命名。",
       suggestions=SERIES_KEYS),
    B("table.transpose", "转置", "关闭：行=元素、列=月份；开启则互换。"),
    I("table.precision.temp", "温度小数位", "", lo=0, hi=6),
    I("table.precision.rainfall", "降水量小数位", "", lo=0, hi=6),
    I("table.precision.raindays", "降水日数小数位", "", lo=0, hi=6),
    I("table.precision.annual", "年值小数位", "", lo=0, hi=6),
    S("table.missing_placeholder", "缺失值占位符", "", suggestions=("—", "-", "", "N/A")),
    B("table.unit_in_label", "标签带单位", "如「日均最低气温 (°C)」。"),
    B("table.include_annual", "年列/年行", "温度取月均、降水取累加。"),
    S("table.annual_label", "年值标签", ""),
    B("table.show_period_note", "附统计时段脚注", ""),
    B("table.show_source_note", "附数据来源脚注", ""),
    B("table.show_title_row", "顶部写标题行", "CSV / XLSX / Markdown 有效。"),
    T("table.title_template", "表格标题模板", ("city", "city_id", "member")),
    T("table.note_template", "时段脚注模板", ("period", "temp_unit", "rain_unit")),
    S("table.xlsx.sheet_name", "工作表名", "最长 31 字符。"),
    B("table.xlsx.freeze_header", "冻结表头", "同时冻结首列。"),
    S("table.xlsx.header_fill", "表头填充色", "8 位 ARGB，如 FF0EBFA2。"),
    S("table.xlsx.header_font_color", "表头字色", "8 位 ARGB，如 FFFFFFFF。"),
    S("table.xlsx.zebra_fill", "隔行底色", "8 位 ARGB，如 FFF3F7FA。"),
    FL("table.xlsx.column_width", "列宽", "", lo=1, step=1),
    FL("table.xlsx.first_column_width", "首列宽", "", lo=1, step=1),
    S("table.xlsx.border_color", "边框色", "8 位 ARGB，如 FFBCC9D4。"),
    S("table.xlsx.number_format", "数字格式", "仅作用于数值单元格。",
      suggestions=("0.0", "0.00", "0", "#,##0.0")),
)

# =========================================================================
# I. compare —— 多城市对比图
# =========================================================================

#: element_styles 的二级子分组：5 个元素 × （图形类型 / 线型 / 标记）
ELEMENT_STYLE_SUBGROUPS: tuple[tuple[str, str], ...] = tuple(
    (key, f"{label}（{key}）") for key, label in SERIES_LABELS.items()
)


def _element_style_fields() -> tuple[Field, ...]:
    """``compare.element_styles.<元素>.{chart_type,linestyle,marker}``（仅 overlay 生效）。"""
    result: list[Field] = []
    for key, _label in SERIES_LABELS.items():
        result.append(_f(
            f"compare.element_styles.{key}.chart_type", "enum_or_null", "图形类型",
            "留空 = 跟随「对比图形类型」（默认跟随状态下降水类画柱、气温类画线）。",
            enum=CHART_TYPE_ENUM, subgroup=key, null_label="跟随对比图形类型（默认）",
        ))
        result.append(_f(
            f"compare.element_styles.{key}.linestyle", "enum", "线型",
            enum=LINESTYLE_ENUM, subgroup=key,
        ))
        result.append(_f(
            f"compare.element_styles.{key}.marker", "enum", "标记",
            enum=MARKER_ENUM, subgroup=key,
        ))
    return tuple(result)


COMPARE_FIELDS: tuple[Field, ...] = (
    E("compare.metric", "对比元素（单元素）", SERIES_ENUM,
      "单个元素；「多元素对比」非空时被覆盖。"),
    _f("compare.metrics", "list_enum", "多元素对比",
       "勾选 2–4 个元素：facet 一格一元素，overlay 同图叠加；"
       "顺序即分面顺序与「同要素一行」图例的列序，只勾 1 个等价于单元素。",
       options=SERIES_ENUM, max_items=4),
    E("compare.mode", "多元素呈现方式",
      (("facet", "分面：一格一元素"), ("overlay", "叠加：同一张图（颜色=城市、线型=元素）")),
      "仅多元素时生效，单元素仍走原单图路径。"),
    _f("compare.grid", "grid", "分面排版（列x行）",
       "auto = 按元素数自动；或「列x行」如 2x2（分隔符 x / × / * / , 均可，前=列、后=行）。"
       "仅 facet 多元素时生效。",
       suggestions=("auto", "1x2", "2x1", "2x2", "3x2", "2x3")),
    _f("compare.facet_figsize", "num2_or_null", "分面画布尺寸",
       "null = 每格约「对比图画布尺寸」的 70% 宽自动推算。"),
    E("compare.share_ylim", "分面纵轴量程",
      (("row", "同一行同单位元素共用量程"), ("none", "各格独立"))),
    _f("compare.hspace", "num_or_null", "分面行间距", "null = 0.34。"),
    _f("compare.wspace", "num_or_null", "分面列间距", "null = 0.22。"),
    E("compare.sort_metric", "多元素排序基准",
      (("", "自动（第一个元素）"),) + SERIES_ENUM,
      "只影响多元素：保证各格城市顺序一致。"),
    E("compare.chart_type", "对比图形类型", CHART_TYPE_ENUM),
    _f("compare.figsize", "num2", "对比图画布尺寸", "单位：英寸 [宽, 高]。"),
    T("compare.title_text", "对比图标题模板",
      TEXT_PLACEHOLDERS + ("metric", "city_count")),
    T("compare.title_text_multi", "多元素对比标题模板",
      TEXT_PLACEHOLDERS + ("metrics", "metric_count", "city_count"),
      "仅多元素时使用；{metrics} 用 - 连接全部元素，{metric_count} 为元素个数。"),
    _f("compare.colors", "list_color", "城市配色序列",
       "按顺序分配给城市；不足则循环或自动生成。"),
    _f("compare.color_map", "str_map", "指定城市颜色",
       "「城市显示名 → 颜色」，命中就固定用该颜色，未命中退回「城市配色序列」轮询。"
       "仅在「多城市对比」模式下生效（多图模式不读它）；"
       "用于让同一城市在多张对比图 / 更换对比元素 / 增删城市后颜色保持稳定。"
       "键必须是城市显示名（受「城市名取短名」影响），不是 cityId；"
       "「取色依据」为 city_id 时本表不生效。"
       "（该键由绘图代码读取，但未列入内置默认值。）",
       key_kind="city", value_kind="color", value_options=COLOR_PRESETS, extra=True),
    E("compare.color_by", "取色依据",
      (("order", "按传入顺序"), ("city_id", "按城市编号"))),
    _f("compare.linestyle_cycle", "list_str", "线型循环（按城市）",
       "按城市顺序轮换：颜色统一（如全黑）时靠它区分城市；"
       "「样式轮换维度」为 metric 时改为按元素轮换。",
       suggestions=("-", "--", "-.", ":")),
    _f("compare.marker_cycle", "list_str", "标记循环（按城市）",
       "同上，按城市顺序轮换标记。",
       suggestions=("o", "s", "^", "D", "v", "P", "X", "*")),
    E("compare.style_dimension", "样式轮换维度",
      (("city", "按城市（颜色统一时靠线型/标记区分城市）"),
       ("metric", "按元素（旧行为：元素样式里的线型/标记生效）"),
       ("hybrid", "线型按城市、标记按元素")),
      "决定线型与标记按哪个维度轮换；柱的填充/纹理跟随同一维度"
      "（city / hybrid 按城市，metric 按元素）。"),
    FL("compare.linewidth", "线宽", "", lo=0, step=0.1),
    FL("compare.markersize", "标记大小", "", lo=0, step=0.5),
    FL("compare.alpha", "透明度", "", lo=0, hi=1, step=0.05),
    FL("compare.bar_width", "总柱宽", "柱状模式下按城市数均分。", lo=0, hi=1, step=0.05),
    E("compare.bar_fill", "柱填充方式（全局兜底）",
      (("solid", "实心（纯色填充）"), ("hollow", "空心（仅边框）"), ("hatch", "纹理填充")),
      "仅柱状对比有效；「柱填充方式（按城市轮换）」非空时被它覆盖。"),
    S("compare.bar_hatch", "柱纹理样式（全局兜底）",
      "matplotlib hatch：/ \\ | - + x o O . *；重复字符可加密（如 ///）；留空 = /。",
      suggestions=("/", "//", "///", "\\\\", "|", "-", "+", "x", "o", "O", ".", "*")),
    _f("compare.bar_fill_cycle", "list_enum", "柱填充方式（按城市轮换）",
       "按城市顺序轮换，如 [实心, 空心, 纹理] 让三个城市同色也能区分；"
       "非空时覆盖上面的全局兜底。",
       options=(("solid", "实心"), ("hollow", "空心（仅边框）"), ("hatch", "纹理填充"))),
    _f("compare.bar_hatch_cycle", "list_str", "柱纹理样式（按城市轮换）",
       "按城市顺序轮换；留空项 = 该城市不加纹理；非空时覆盖全局兜底。",
       suggestions=("/", "//", "///", "\\\\", "x", "o", "O", ".", "*")),
    C("compare.bar_edgecolor", "柱边线色", "", specials=("none",)),
    FL("compare.bar_linewidth", "柱边线宽", "空心时留 0 会自动取 1.2。", lo=0, step=0.1),
    E("compare.sort_by", "排序方式",
      (("none", "不排序"), ("value_desc", "数值降序"), ("value_asc", "数值升序"),
       ("name", "按城市名"), ("city_id", "按城市编号"))),
    B("compare.show_value_range", "图例显示数值范围", ""),
    T("compare.label_template", "图例名模板", ("city",)),
    I("compare.legend_ncol", "图例列数（旧键）", "0 = 自动；多元素时以「多元素图例列数」优先。",
      lo=0, hi=20),
    E("compare.legend.position", "多元素图例位置",
      (("inside", "坐标区内"), ("bottom", "横轴下方"), ("top", "坐标区上方")),
      "仅多元素时生效。"),
    E("compare.legend.layout", "多元素图例排列",
      (("city", "同城一行"), ("metric", "同要素一行"), ("auto", "自然顺序")),
      "仅多元素时生效。"),
    FL("compare.legend.gap", "图例与横轴距离",
       "坐标区高度比例；position=inside 时为区内相对位置。", lo=0, hi=1, step=0.01),
    FL("compare.legend.reserve", "底部预留比例",
       "position=bottom 时给图例腾出的下边距（整图比例）。", lo=0, hi=0.8, step=0.01),
    I("compare.legend.ncol", "多元素图例列数", "0 = 按排列方式自动。", lo=0, hi=20),
    B("compare.data_labels.show", "显示数值标签", ""),
    FL("compare.data_labels.fontsize", "数值标签字号", "", lo=1, step=0.5),
    S("compare.data_labels.format", "数值标签格式", "",
      suggestions=("{:.1f}", "{:.0f}", "{:.2f}")),
    FL("compare.data_labels.offset", "数值标签偏移", "", step=1, unit="点"),
) + _element_style_fields()

# =========================================================================
# J. multi —— 多图（多城市同画布）
# =========================================================================

MULTI_FIELDS: tuple[Field, ...] = (
    _f("multi.grid", "grid", "排列方式",
       "auto = 1×N 全横排；或「列x行」，如 2x2、3x2（分隔符 x / × / * / , 均可，前=列数、后=行数）。按行优先填充。",
       suggestions=("auto", "1x2", "2x1", "2x2", "3x2", "2x3", "3x3")),
    E("multi.legend", "图例方式",
      (("figure", "整幅只画一个图例"), ("per_chart", "每格各一个"), ("none", "不画"))),
    E("multi.share_ylim", "纵轴量程",
      (("row", "同一行共用一套量程（便于横向比较）"), ("none", "各图独立"))),
    _f("multi.figsize", "num2_or_null", "整幅画布尺寸",
       "null = 单个画布尺寸 × (列, 行)，保证每格绘图区与单图一致。"),
    _f("multi.wspace", "num_or_null", "列间距", "null = 交给布局引擎决定。"),
    _f("multi.hspace", "num_or_null", "行间距", "null = 交给布局引擎决定。"),
)

#: ``figure.legend.*`` 的 14 个参数在表单里单独成组（单图 / 对比图 / 多图通用）
FIGURE_FIELDS = tuple(
    Field(**{**f.__dict__, "subgroup": "legend"})
    if f.path.startswith("figure.legend.") else f
    for f in FIGURE_FIELDS
)

# =========================================================================
# 分组
# =========================================================================

GROUPS: tuple[Group, ...] = (
    Group("meta", "0. 配置标识",
          "配置名由文件名 / profiles 键决定，不可在表单中直接改；说明文案会随配置一起保存。",
          (
              _f("profile_name", "str", "配置名",
                 "所选配置的名称（只读）。另存为新配置时在此填写新名称。", readonly=True),
              S("profile_description", "配置说明", "随配置一起保存，用于配置列表中的提示。"),
          )),
    Group("data", "A. 数据与字段口径",
          "语言、单位、缺失值策略、经纬度显示等取数与口径相关设置。",
          DATA_FIELDS),
    Group("fetch", "B. 请求层",
          "数据源地址、超时、重试与退避、限速、代理、请求头与缓存。",
          FETCH_FIELDS),
    Group("output", "C. 输出与文件",
          "输出目录、文件名模板、表格与图片格式、覆盖策略。",
          OUTPUT_FIELDS),
    Group("figure", "D. 画布与版式",
          "画布尺寸、字体、标题、副标题、图例、网格、背景色带、标注与署名。"
          "图例参数集中在本组的「图例」小节，单图 / 对比图 / 多图通用。",
          FIGURE_FIELDS, subgroups=(("legend", "图例（单图 / 对比图 / 多图通用）"),)),
    Group("axes_primary", "E1. 主轴（温度）",
          "左轴：量程、刻度、轴标题与轴脊。", AXES_FIELDS, axes=True),
    Group("axes_secondary", "E2. 副轴（降水）",
          "右轴：量程、刻度、轴标题与轴脊。", AXES_FIELDS, axes=True),
    Group("axes_tertiary", "E3. 第三轴（降水日数）",
          "再向外偏移一条右轴，用于量级差异大的元素并置显示。", AXES_FIELDS, axes=True),
    Group("axes_x", "F. 横轴（月份轴）",
          "月份标签样式、间隔、旋转与竖分隔线。", AXES_X_FIELDS, prefix="axes_x"),
    Group("series", "G. 绘图元素",
          "最低气温 / 最高气温 / 平均气温 / 降水量 / 降水日数，每个元素独立配置。",
          SERIES_FIELDS, series=True),
    Group("table", "H. 表格",
          "行项目与顺序、行名、转置、精度、脚注与 XLSX 样式。", TABLE_FIELDS),
    Group("compare", "I. 多城市对比图",
          "单元素 / 多元素对比：分面或同图叠加、配色排序、图例与数值标签。"
          "图例的样式（字号、边框、底色、标题）在 D 组「图例」里；本组只放多元素专属的"
          "排列方式与位置。",
          COMPARE_FIELDS, subgroups=ELEMENT_STYLE_SUBGROUPS),
    Group("multi", "J. 多图（多城市同画布）",
          "排列方式、图例方式、纵轴量程策略与间距。", MULTI_FIELDS),
)


# ---- 索引与展开 ----------------------------------------------------------

def series_fields(key: str) -> tuple[Field, ...]:
    """把 ``SERIES_FIELDS`` 的相对路径展开为 ``series.<key>.<子项>``。"""
    return tuple(
        Field(**{**f.__dict__, "path": f"series.{key}.{f.path}", "subgroup": key})
        for f in SERIES_FIELDS
    )


def axes_fields(prefix: str) -> tuple[Field, ...]:
    """把 ``AXES_FIELDS`` 展开为 ``<prefix>.<子项>``；第三轴保留 ``offset_points``。"""
    result: list[Field] = []
    for f in AXES_FIELDS:
        if f.path == "offset_points" and prefix != "axes_tertiary":
            continue
        result.append(Field(**{**f.__dict__, "path": f"{prefix}.{f.path}"}))
    return tuple(result)


def all_fields() -> list[Field]:
    """全部字段（含分组展开后的完整点路径）。重复路径只保留第一次出现。"""
    seen: dict[str, Field] = {}
    for group in GROUPS:
        for f in group_fields(group):
            seen.setdefault(f.path, f)
    return list(seen.values())


def empty_value(field: Field) -> Any:
    """该字段「未设置」时的类型正确空值。

    用于配置里**没有**这个键的场合（典型是两个扩展键）：给了类型正确的空值，
    表单装载后就不会因为「`null` 与 `""` / `{}` 不等」而误报「已修改」。
    """
    kind = field.kind
    if kind in ("str_map",):
        return {}
    if kind in ("list_enum", "list_str", "list_color"):
        return []
    if kind == "json":
        return []
    if kind == "bool":
        return False
    if kind == "num2":
        return [] if field.allow_empty else None
    if kind in ("int", "float", "int_or_null", "num_or_null", "num2_or_null"):
        return None
    if kind == "enum_or_null":
        return None
    if kind == "enum":
        return field.enum[0][0] if field.enum else None
    return ""


_FIELD_INDEX: Optional[dict[str, Field]] = None


def field_index() -> dict[str, Field]:
    """点路径 → Field 的索引（惰性构建）。"""
    global _FIELD_INDEX
    if _FIELD_INDEX is None:
        _FIELD_INDEX = {f.path: f for f in all_fields()}
    return _FIELD_INDEX


def extra_paths() -> set[str]:
    """被绘图代码读取、但未列入 ``DEFAULTS`` 的扩展键。"""
    return {f.path for f in all_fields() if f.extra}


def schema_payload() -> dict[str, Any]:
    """给前端的完整 Schema。"""
    return {
        "groups": [
            {**group.to_dict(),
             **({"seriesFields": [f.to_dict() for f in SERIES_FIELDS],
                 "seriesKeys": [
                     {"value": k, "label": f"{SERIES_LABELS[k]}（{k}）"} for k in SERIES_KEYS
                 ]} if group.series else {}),
             **({"axesFields": [f.to_dict() for f in axes_fields(group.key)]}
                if group.axes else {})}
            for group in GROUPS
        ],
    }


# ---- 取值规范化与校验 ----------------------------------------------------

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
#: 与 core.config_loader.parse_grid 保持一致的允许分隔符（全半角、大小写）
_GRID_RE = re.compile(r"^\s*(\d+)\s*[xX×✕*,，]\s*(\d+)\s*$")
_NAMED_COLORS = {
    "black", "white", "red", "green", "blue", "yellow", "orange", "purple", "pink",
    "brown", "gray", "grey", "cyan", "magenta", "lime", "navy", "teal", "olive",
    "maroon", "silver", "gold", "transparent",
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _to_number(raw: Any, path: str) -> float:
    if _is_number(raw):
        return float(raw)
    if isinstance(raw, str) and raw.strip() != "":
        try:
            return float(raw.strip())
        except ValueError as exc:
            raise FieldError(path, f"需要数字，收到 {raw!r}") from exc
    raise FieldError(path, f"需要数字，收到 {raw!r}")


def _to_int(raw: Any, path: str) -> int:
    value = _to_number(raw, path)
    if abs(value - round(value)) > 1e-9:
        raise FieldError(path, f"需要整数，收到 {raw!r}")
    return int(round(value))


def _check_range(f: Field, value: float) -> None:
    if f.min is not None and value < f.min:
        raise FieldError(f.path, f"不能小于 {f.min}（当前 {value}）")
    if f.max is not None and value > f.max:
        raise FieldError(f.path, f"不能大于 {f.max}（当前 {value}）")


def _norm_color(f: Field, raw: Any) -> str:
    text = str(raw if raw is not None else "").strip()
    if text in f.specials:
        return text
    lowered = text.lower()
    if _HEX_RE.match(text) or lowered in _NAMED_COLORS:
        return text
    # matplotlib 还接受 "C0"~"C9" 与 "tab:blue" 之类的颜色
    if re.match(r"^C\d$", text) or lowered.startswith("tab:"):
        return text
    allowed = "、".join(f.specials) if f.specials else ""
    hint = f"，或 {allowed}" if allowed else ""
    raise FieldError(f.path, f"不是合法颜色（应为 #RRGGBB / 颜色名{hint}），收到 {text!r}")


def _norm_template(f: Field, raw: Any) -> str:
    text = str(raw if raw is not None else "")
    unknown: list[str] = []
    for name in re.findall(r"\{([^{}]*)\}", text):
        base = name.split(":")[0].split("!")[0].strip()
        if base and base not in f.placeholders:
            unknown.append(base)
    if unknown:
        allowed = " ".join("{" + p + "}" for p in f.placeholders)
        raise FieldError(
            f.path,
            f"含未知占位符 {', '.join(sorted(set(unknown)))}；可用：{allowed}",
        )
    return text


def _norm_color_list(f: Field, raw: Any) -> list[str]:
    items = _as_list(f, raw)
    result: list[str] = []
    for index, item in enumerate(items):
        probe = Field(path=f"{f.path}[{index}]", kind="color", label=f.label,
                      specials=f.specials)
        result.append(_norm_color(probe, item))
    return result


def _as_list(f: Field, raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    raise FieldError(f.path, f"需要数组，收到 {raw!r}")


def normalize_value(f: Field, raw: Any) -> Any:
    """把一个原始输入规范化为该字段的数据类型；非法时抛 :class:`FieldError`。

    这是「按数据类型进行输入验证」的服务端权威实现：网页可以即时提示，
    但即使绕过前端，非法值也进不了渲染流程。
    """
    kind = f.kind

    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
            return raw.strip().lower() == "true"
        raise FieldError(f.path, f"需要布尔值 true/false，收到 {raw!r}")

    if kind == "int":
        value = _to_int(raw, f.path)
        _check_range(f, value)
        return value

    if kind == "float":
        value = _to_number(raw, f.path)
        _check_range(f, value)
        return value

    if kind == "int_or_null":
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            return None
        value = _to_int(raw, f.path)
        _check_range(f, value)
        return value

    if kind == "num_or_null":
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            return None
        value = _to_number(raw, f.path)
        _check_range(f, value)
        return value

    if kind == "num2":
        items = _as_list(f, raw)
        if not items:
            if f.allow_empty:
                return []
            raise FieldError(f.path, "需要两个数字，如 [12, 6]")
        if len(items) != 2:
            raise FieldError(f.path, f"需要恰好两个数字（如 [12, 6]），收到 {len(items)} 个")
        lo = _to_number(items[0], f.path)
        hi = _to_number(items[1], f.path)
        return [lo, hi]

    if kind == "num2_or_null":
        items = _as_list(f, raw)
        if not items:
            return None
        if len(items) != 2:
            raise FieldError(f.path, f"需要两个数字（如 [12, 6]）或 null，收到 {len(items)} 个")
        return [_to_number(items[0], f.path), _to_number(items[1], f.path)]

    if kind == "enum_or_null":
        # 空值（null / 空串）表示「不指定」，交给绘图代码回落默认
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            return None
        for value, _label in f.enum:
            if str(raw) == str(value):
                return value
        allowed = "、".join(str(v) for v, _ in f.enum)
        raise FieldError(f.path, f"只能是 {allowed} 之一或留空，收到 {raw!r}")

    if kind == "enum":
        # null 视作空串：部分枚举本身就有 "" 选项（如 marker「不显示」）
        text = "" if raw is None else str(raw)
        for value, _label in f.enum:
            if f.coerce == "int":
                try:
                    if _to_int(raw, f.path) == int(value):
                        return int(value)
                except FieldError:
                    continue
            elif text == str(value):
                return value
        allowed = "、".join(str(v) for v, _ in f.enum)
        raise FieldError(f.path, f"只能是 {allowed} 之一，收到 {raw!r}")

    if kind == "color":
        return _norm_color(f, raw)

    if kind == "template":
        return _norm_template(f, raw)

    if kind == "grid":
        text = str(raw if raw is not None else "").strip()
        if text.lower() in ("", "auto"):
            return "auto"
        match = _GRID_RE.match(text)
        if match is None:
            raise FieldError(
                f.path,
                f"应为 auto 或「列x行」（如 2x2、3x2；分隔符 x / × / * / , 均可），收到 {text!r}",
            )
        cols, rows = int(match.group(1)), int(match.group(2))
        if cols < 1 or rows < 1:
            raise FieldError(f.path, f"行列数必须为正整数，收到 {text!r}")
        return f"{cols}x{rows}"

    if kind in ("str", "path"):
        if raw is None:
            return ""
        if isinstance(raw, (dict, list)):
            raise FieldError(f.path, f"需要文本，收到 {type(raw).__name__}")
        return str(raw)

    if kind == "rotation":
        if isinstance(raw, bool):
            raise FieldError(f.path, "需要角度数字，或 vertical / horizontal")
        if _is_number(raw):
            return raw
        text = str(raw).strip()
        if text in f.text_options:
            return text
        try:
            return _to_int(text, f.path) if text.lstrip("+-").isdigit() else float(text)
        except (ValueError, FieldError) as exc:
            raise FieldError(
                f.path, f"需要角度数字，或 {' / '.join(f.text_options)}，收到 {raw!r}"
            ) from exc

    if kind == "list_enum":
        items = _as_list(f, raw)
        allowed = [value for value, _ in f.options]
        result: list[Any] = []
        for item in items:
            match = next((v for v in allowed if str(v) == str(item).strip()), None)
            if match is None:
                raise FieldError(
                    f.path, f"含不支持的取值 {item!r}；可选 {'、'.join(str(a) for a in allowed)}"
                )
            if match not in result:
                result.append(match)
        if f.max_items and len(result) > f.max_items:
            raise FieldError(f.path, f"最多选择 {f.max_items} 项（当前 {len(result)} 项）")
        if not result and not f.allow_empty:
            raise FieldError(f.path, "至少要选择一项")
        return result

    if kind == "list_str":
        items = _as_list(f, raw)
        return [str(item) for item in items if str(item).strip() != ""]

    if kind == "list_color":
        return _norm_color_list(f, raw)

    if kind == "str_map":
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise FieldError(f.path, f"需要「键: 值」对象，收到 {type(raw).__name__}")
        result: dict[str, str] = {}
        for key, value in raw.items():
            text = "" if value is None else str(value).strip()
            if f.value_kind == "color" and text:
                # 值声明为颜色时逐个验色（留空表示「未设置」，交给前端提示）
                probe = Field(path=f"{f.path}「{key}」", kind="color", label=f.label,
                              specials=f.specials)
                text = _norm_color(probe, text)
            result[str(key)] = text
        return result

    if kind == "json":
        return raw

    raise FieldError(f.path, f"未知的字段类型 {kind!r}")


def validate_value(f: Field, raw: Any) -> list[str]:
    """返回该字段的校验错误列表（空列表 = 通过）。"""
    try:
        normalize_value(f, raw)
    except FieldError as exc:
        return [exc.message]
    return []


# 未知键告警里形如「以下配置项不是已知项…：a.b, c.d」的前缀
_UNKNOWN_PREFIX = "以下配置项不是已知项"


def filter_unknown_warnings(warnings: Iterable[str]) -> list[str]:
    """剔除由「扩展键」引起的未知项告警（其余告警原样保留）。

    ``axes_x.label_text`` 与 ``compare.color_map`` 会被绘图代码读取，
    但不在 ``DEFAULTS`` 中，``validate_config`` 会把它们报成未知项。
    表单既然允许编辑它们，就不该再提示「请核对拼写」。
    """
    result: list[str] = []
    for message in warnings:
        if not message.startswith(_UNKNOWN_PREFIX):
            result.append(message)
            continue
        _, _, tail = message.partition("：")
        remaining = [
            item.strip()
            for item in tail.split(",")
            if item.strip() and item.strip() not in extra_paths()
        ]
        if remaining:
            result.append(f"{_UNKNOWN_PREFIX}，已被忽略（请核对拼写）：" + ", ".join(remaining))
    return result
