"""前端静态自检（不需要 Node / 浏览器）。

检查四件事：
1. **括号配平**：三个 JS 文件的圆括号/花括号/方括号在剔除字符串与注释后必须配平
   （可捕捉到最容易被忽略的语法级错误）。
2. **DOM 引用有效**：JS 里 ``$("#xxx")`` 引用的每个 id 都必须在 index.html 中存在。
3. **脚本与样式齐全**：index.html 引用的静态资源文件真实存在。
4. **页签/日志级别一致**：``data-tab`` 与 ``data-pane`` 一一对应；
   JS 里的默认日志级别与页面勾选项一致。

运行：``python tests/test_frontend.py``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

PASSED = 0
FAILED: list[str] = []


def check(condition: bool, message: str) -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(message)


#: 出现在这些标记之后的 ``/`` 才可能是正则字面量（而不是除号）
REGEX_AFTER = set("(,=:[!&|?{};+-*%~^<>")
REGEX_AFTER_WORDS = {
    "return", "typeof", "instanceof", "in", "of", "case", "do", "else", "new",
    "delete", "void", "yield", "await",
}


def strip_js(code: str) -> str:
    """剔除字符串字面量、正则字面量与注释，只留下结构性字符。

    必须识别正则字面量：``/[\\\\/:*?"<>|]/`` 里的引号如果被当成字符串起点，
    后面的括号配对就会被误判。
    """
    out: list[str] = []
    index = 0
    length = len(code)
    prev = ""            # 上一个有意义的字符
    word = ""            # 正在累积的标识符

    while index < length:
        char = code[index]

        if char == "/" and index + 1 < length and code[index + 1] == "/":
            while index < length and code[index] != "\n":
                index += 1
            continue
        if char == "/" and index + 1 < length and code[index + 1] == "*":
            index += 2
            while index + 1 < length and not (code[index] == "*" and code[index + 1] == "/"):
                index += 1
            index += 2
            continue

        if char in ('"', "'", "`"):
            quote = char
            index += 1
            while index < length:
                if code[index] == "\\":
                    index += 2
                    continue
                if code[index] == quote:
                    index += 1
                    break
                index += 1
            out.append('""')
            prev = '"'
            word = ""
            continue

        if char == "/" and (prev in REGEX_AFTER or word in REGEX_AFTER_WORDS):
            cursor = index + 1
            in_class = False
            while cursor < length:
                current = code[cursor]
                if current == "\\":
                    cursor += 2
                    continue
                if current == "[":
                    in_class = True
                elif current == "]":
                    in_class = False
                elif current == "/" and not in_class:
                    break
                elif current == "\n":
                    break
                cursor += 1
            index = cursor + 1
            while index < length and code[index].isalpha():
                index += 1
            out.append("/RE/")
            prev = "/"
            word = ""
            continue

        if char.isalnum() or char in "_$":
            word += char
        elif not char.isspace():
            word = ""
        if not char.isspace():
            prev = char
        out.append(char)
        index += 1
    return "".join(out)


JS_FILES = ["js/api.js", "js/form.js", "js/app.js"]

html = (WEB / "index.html").read_text(encoding="utf-8")
html_ids = set(re.findall(r'id="([^"]+)"', html))

# ---- 1. 括号配平 ---------------------------------------------------------

for rel in JS_FILES:
    path = WEB / rel
    check(path.is_file(), f"缺少前端文件：{rel}")
    if not path.is_file():
        continue
    stripped = strip_js(path.read_text(encoding="utf-8"))
    for opener, closer in (("(", ")"), ("{", "}"), ("[", "]")):
        depth = 0
        minimum = 0
        for char in stripped:
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                minimum = min(minimum, depth)
        check(depth == 0, f"{rel} 的 {opener}{closer} 不配平（差值 {depth}）")
        check(minimum >= 0, f"{rel} 的 {opener}{closer} 出现提前闭合")

# ---- 2. DOM 引用 ---------------------------------------------------------

for rel in JS_FILES:
    path = WEB / rel
    if not path.is_file():
        continue
    code = path.read_text(encoding="utf-8")
    used = set(re.findall(r'\$\("#([A-Za-z0-9_\-]+)"\)', code))
    used |= set(re.findall(r"getElementById\(['\"]([A-Za-z0-9_\-]+)['\"]\)", code))
    missing = sorted(used - html_ids)
    check(not missing, f"{rel} 引用了 index.html 中不存在的 id：{missing}")

# 反向：页面上的关键按钮都应有 JS 绑定（避免出现"点了没反应"的死按钮）
for element_id in ("btn-run", "btn-save", "btn-save-as", "btn-marks", "btn-cache",
                   "btn-cancel", "btn-fit", "btn-download-chart", "profile-select",
                   "city-input", "config-search", "no-save-toggle"):
    check(element_id in html_ids, f"index.html 缺少关键元素 #{element_id}")
    bound = any(
        f'#{element_id}"' in (WEB / rel).read_text(encoding="utf-8")
        for rel in JS_FILES
    )
    check(bound, f"#{element_id} 没有对应的 JS 绑定")

# ---- 3. 静态资源 ---------------------------------------------------------

assets = re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
check(bool(assets), "index.html 应引用至少一个静态资源")
for asset in assets:
    target = WEB / asset[len("/static/"):]
    check(target.is_file(), f"index.html 引用的静态资源不存在：{asset}")

for rel in ("css/style.css", *JS_FILES):
    text = (WEB / rel).read_text(encoding="utf-8")
    check('"use strict"' in text or ":root" in text, f"{rel} 缺少必要的头部声明")

# ---- 4. 页签与日志级别 ---------------------------------------------------

tabs = set(re.findall(r'data-tab="([^"]+)"', html))
panes = set(re.findall(r'data-pane="([^"]+)"', html))
check(tabs == panes, f"data-tab 与 data-pane 不一致：tab={sorted(tabs)} pane={sorted(panes)}")

html_levels = set(re.findall(r'class="log-level" value="([a-z]+)"', html))
app_code = (WEB / "js/app.js").read_text(encoding="utf-8")
css_code = (WEB / "css" / "style.css").read_text(encoding="utf-8")
defaults = re.search(r"logLevels: new Set\(\[([^\]]*)\]\)", app_code)
check(defaults is not None, "app.js 应声明默认日志级别集合")
if defaults:
    levels = set(re.findall(r'"([a-z]+)"', defaults.group(1)))
    check(levels <= html_levels, f"默认日志级别 {sorted(levels)} 超出页面可选 {sorted(html_levels)}")
    check(levels == html_levels,
          f"默认日志级别应与页面勾选项一致：{sorted(levels)} vs {sorted(html_levels)}")

# ---- 5. 多元素对比界面 ---------------------------------------------------

form_code = (WEB / "js" / "form.js").read_text(encoding="utf-8")

check('id="metric-checks"' in html and 'id="compare-mode"' in html,
      "index.html 应提供多元素勾选组与呈现方式下拉")
check("compare_metrics" in app_code and "compare_mode" in app_code,
      "app.js 应把多元素与呈现方式一起提交")
check("metric-input" not in app_code + html,
      "旧的单选 compare_metric 控件应已移除")
check(app_code.count("metric-checks") >= 2,
      "app.js 应读取并约束 #metric-checks 的勾选数量")
check("picked >= 4" in app_code and "已选满" in app_code,
      "app.js 应在勾满 4 个后禁用其余选项")

check(form_code.count('case "enum_or_null"') >= 2,
      "form.js 应在校验与控件两处都处理 enum_or_null")
check("maxItems" in form_code, "form.js 应处理 list_enum 的 maxItems 上限")
check("mainRows" in form_code,
      "二级子分组下的主区字段应参与过滤（applyFilters 需覆盖 mainRows）")
check("field.subgroup" in form_code, "form.js 应按 field.subgroup 归组")

# ---- 5. 「上次使用的配置」关键逻辑存在性 ---------------------------------

check('"/api/state"' in app_code, "app.js 应通过 /api/state 记住并恢复上次使用的配置")
check("function pickInitialProfile" in app_code
      and "function loadInitialProfile" in app_code
      and "function rememberProfile" in app_code,
      "app.js 应实现初始配置选择、失败回退与记忆三件事")
check(app_code.count("rememberProfile(") >= 2,
      "rememberProfile 应既被定义也被调用（加载配置后记住它）")
check("saved && saved.profile" in app_code and "saved.lastProfile" in app_code,
      "初始配置应优先用服务端算好的 profile，并保留 lastProfile 兜底")
check("loadInitialProfile(preferred, fallback)" in app_code,
      "初始化应按「首选 + 回退」两步加载")
check(html_ids and "profile-select" in html_ids, "页面应有配置选择框（用于显示当前配置）")
check("已改用默认配置" in app_code, "首选配置加载失败时应有回退提示（回退到默认配置）")
check('API.get("/api/state").catch(' in app_code,
      "读取状态失败时应能降级，不影响启动")
check("已回退到" in app_code,
      "服务端把配置回退到默认时应如实提示（避免「以为在编辑 A、其实编辑的是默认」）")

# ---- 5.5 左右分栏（拖动改宽，左栏按窗口 1/3 ~ 1/2 夹紧） ----------------

check("splitter" in html_ids, "index.html 应有分隔条 #splitter")
check("col-resize" in css_code, "分隔条应显示为可拖动（cursor: col-resize）")
check("--left-width" in app_code and "--left-width" in css_code,
      "左右宽度应由 CSS 变量 --left-width 控制（JS 设置、CSS 使用）")
check("SPLIT_MIN_RATIO = 1 / 3" in app_code and "SPLIT_MAX_RATIO = 1 / 2" in app_code,
      "左栏宽度约束应是窗口宽度的 1/3 ~ 1/2")
check("Math.min(SPLIT_MAX_RATIO, Math.max(SPLIT_MIN_RATIO" in app_code,
      "必须有夹紧逻辑，保证拖不出 1/3 ~ 1/2 的范围")
check("window.innerWidth" in app_code, "宽度基准应是窗口宽度（而非容器宽度）")
check("pointerdown" in app_code and "pointermove" in app_code and "pointerup" in app_code,
      "应支持指针拖动")
check("dblclick" in app_code and "ArrowLeft" in app_code and "ArrowRight" in app_code,
      "应支持双击复位与方向键微调")
check("saved.leftRatio" in app_code and "leftRatio: clamped" in app_code,
      "左右宽度应持久化并在启动时恢复")
check(".splitter { display: none; }" in css_code,
      "窄屏（上下排布）时应隐藏分隔条")

# 服务端提供的库内类型（kind）都应在表单里有对应分支，避免出现"无法编辑"的字段
schema_code = (ROOT / "app" / "schema.py").read_text(encoding="utf-8")
kinds = set(re.findall(r'"(?:kind=)?"(bool|int|float|int_or_null|num_or_null|num2|num2_or_null|'
                       r'enum|color|template|path|str|rotation|grid|list_enum|list_str|'
                       r'list_color|str_map|json)"\)', schema_code))
form_code = (WEB / "js" / "form.js").read_text(encoding="utf-8")
unhandled = sorted(
    kind for kind in kinds
    if f'case "{kind}"' not in form_code and f'"{kind}"' not in form_code.split("switch (kind)")[1]
)
check(not unhandled, f"以下字段类型在前端没有控件分支：{unhandled}")

# ---- 结果 ----------------------------------------------------------------

print(f"断言通过 {PASSED} 项，失败 {len(FAILED)} 项")
for message in FAILED:
    print(f"  [失败] {message}")
sys.exit(1 if FAILED else 0)
