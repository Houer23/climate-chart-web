"use strict";

/* =========================================================================
 * Schema 驱动的配置表单
 *
 * * 每个配置项按**数据类型**渲染合适的控件（开关 / 数字框 / 下拉 / 取色器 /
 *   模板框 / 双数字框 / 多选框组 / 列表 / 键值表 / JSON 编辑器）。
 * * 前端即时校验与后端 `app/schema.py` 的规则一一对应；后端仍是权威校验。
 * * 脏检查：与加载时的基线逐项比较，标出「已修改」并支持单项还原。
 * ========================================================================= */

const NAMED_COLORS = new Set([
  "black", "white", "red", "green", "blue", "yellow", "orange", "purple", "pink",
  "brown", "gray", "grey", "cyan", "magenta", "lime", "navy", "teal", "olive",
  "maroon", "silver", "gold", "transparent",
]);
const HEX_RE = /^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;
const GRID_RE = /^\s*(\d+)\s*[xX×✕*,，]\s*(\d+)\s*$/;
const PLACEHOLDER_RE = /\{([^{}]*)\}/g;

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** 是否是可用作绘图的颜色字面量（与后端 color 规则一致）。 */
function isColorValue(text) {
  const value = String(text === null || text === undefined ? "" : text).trim();
  if (!value) return false;
  const lowered = value.toLowerCase();
  return HEX_RE.test(value) || NAMED_COLORS.has(lowered)
    || /^C\d$/.test(value) || lowered.startsWith("tab:");
}

/** 单项校验：返回错误文案或 null（与后端规则保持一致）。 */
function validateField(field, value) {
  const kind = field.kind;
  const checkRange = (number) => {
    if (isNumber(field.min) && number < field.min) return `不能小于 ${field.min}（当前 ${number}）`;
    if (isNumber(field.max) && number > field.max) return `不能大于 ${field.max}（当前 ${number}）`;
    return null;
  };

  switch (kind) {
    case "bool":
      return typeof value === "boolean" ? null : `需要布尔值 true/false`;
    case "bool_or_null":
      // null = 跟随上层（全局）设置
      if (value === null || value === undefined) return null;
      return typeof value === "boolean" ? null : "需要 true / false，或留空表示跟随";
    case "int": {
      if (!isNumber(value)) return "需要整数";
      if (!Number.isInteger(value)) return "需要整数（不能带小数）";
      return checkRange(value);
    }
    case "float": {
      if (!isNumber(value)) return "需要数字";
      return checkRange(value);
    }
    case "int_or_null":
    case "num_or_null": {
      if (value === null) return null;
      if (!isNumber(value)) return "需要数字或留空（自动）";
      if (kind === "int_or_null" && !Number.isInteger(value)) return "需要整数";
      return checkRange(value);
    }
    case "num2": {
      if (!Array.isArray(value)) return "需要两个数字";
      if (value.length === 0) return field.allowEmpty ? null : "需要两个数字，如 [12, 6]";
      if (value.length !== 2) return `需要恰好两个数字（当前 ${value.length} 个）`;
      return value.every(isNumber) ? null : "需要两个数字";
    }
    case "num2_or_null": {
      if (value === null) return null;
      if (!Array.isArray(value)) return "需要两个数字或留空";
      if (value.length !== 2) return "需要两个数字";
      return value.every(isNumber) ? null : "需要两个数字";
    }
    case "enum_or_null": {
      // null / 空串 = 不指定，交给后端回落默认
      if (value === null || value === undefined || String(value).trim() === "") return null;
      return field.enum.some((item) => String(item.value) === String(value))
        ? null : `只能是 ${field.enum.map((i) => i.value).join("、")} 之一，或留空`;
    }
    case "enum": {
      if (field.coerce === "int") {
        const number = Number(value);
        return field.enum.some((item) => Number(item.value) === number)
          ? null : `只能是 ${field.enum.map((i) => i.value).join("、")} 之一`;
      }
      return field.enum.some((item) => String(item.value) === String(value))
        ? null : `只能是 ${field.enum.map((i) => i.value).join("、")} 之一`;
    }
    case "color": {
      const text = String(value === null || value === undefined ? "" : value).trim();
      if (field.specials.includes(text)) return null;
      const lowered = text.toLowerCase();
      if (HEX_RE.test(text) || NAMED_COLORS.has(lowered)) return null;
      if (/^C\d$/.test(text) || lowered.startsWith("tab:")) return null;
      const hint = field.specials.length ? `，或 ${field.specials.join(" / ")}` : "";
      return `不是合法颜色（#RRGGBB / 颜色名${hint}）`;
    }
    case "template":
    case "path": {
      const text = String(value === null || value === undefined ? "" : value);
      const unknown = [];
      let match;
      PLACEHOLDER_RE.lastIndex = 0;
      while ((match = PLACEHOLDER_RE.exec(text)) !== null) {
        const base = match[1].split(":")[0].split("!")[0].trim();
        if (base && !field.placeholders.includes(base)) unknown.push(base);
      }
      return unknown.length
        ? `含未知占位符 ${Array.from(new Set(unknown)).join(", ")}`
        : null;
    }
    case "str":
      return isPlainObject(value) || Array.isArray(value) ? "需要文本" : null;
    case "rotation": {
      if (isNumber(value)) return null;
      const text = String(value).trim();
      if (field.textOptions.includes(text)) return null;
      return /^[+-]?\d+(\.\d+)?$/.test(text)
        ? null : `需要角度数字，或 ${field.textOptions.join(" / ")}`;
    }
    case "grid": {
      const text = String(value === null || value === undefined ? "" : value).trim();
      if (text.toLowerCase() === "" || text.toLowerCase() === "auto") return null;
      return GRID_RE.test(text) ? null : `应为 auto 或「列x行」（如 2x2、3x2）`;
    }
    case "list_enum": {
      if (!Array.isArray(value)) return "需要数组";
      const allowed = field.options.map((item) => String(item.value));
      const bad = value.filter((item) => !allowed.includes(String(item)));
      if (bad.length) return `含不支持的取值 ${bad.join("、")}`;
      if (field.maxItems && value.length > field.maxItems) {
        return `最多选择 ${field.maxItems} 项（当前 ${value.length} 项）`;
      }
      return value.length === 0 && !field.allowEmpty ? "至少要选择一项" : null;
    }
    case "list_str":
      return Array.isArray(value) ? null : "需要数组";
    case "list_color": {
      if (!Array.isArray(value)) return "需要数组";
      for (const item of value) {
        const error = validateField({ ...field, kind: "color" }, item);
        if (error) return `第 ${value.indexOf(item) + 1} 项：${error}`;
      }
      return null;
    }
    case "str_map":
      return isPlainObject(value) ? null : "需要「键: 值」对象";
    case "json":
      return null;
    default:
      return null;
  }
}

/* ========================================================================= */

class ConfigForm {
  /**
   * @param {HTMLElement} root  表单容器
   * @param {{onChange:Function, onDirtyChange:Function}} handlers
   */
  constructor(root, handlers) {
    this.root = root;
    this.handlers = handlers || {};
    this.groups = [];
    this.fields = [];
    this.byPath = new Map();
    this.rows = new Map();          // path -> {node, read, write, field}
    this.values = {};
    this.baseline = {};
    this.serverErrors = new Map();
    this.keyword = "";
    this.onlyChanged = false;
    this.cityDatalists = [];        // 需要按城市候选刷新的 <datalist>
    this.cityHintRefreshers = [];   // 模式/城市集合变化后需要重算行内提示的回调
  }

  /* ---------------- 装载 Schema 与取值 ---------------- */

  setSchema(payload) {
    this.groups = payload.groups || [];
    this.fields = [];
    this.byPath = new Map();
    this.groups.forEach((group) => {
      (group.fields || []).forEach((field) => {
        field.groupKey = group.key;
        field.groupTitle = group.title;
        this.fields.push(field);
        this.byPath.set(field.path, field);
      });
    });
    this.build();
  }

  setValues(values) {
    this.values = deepCopy(values) || {};
    this.baseline = deepCopy(values) || {};
    this.serverErrors.clear();
    this.writeAll();
  }

  /** 快捷微调等服务端回调：把新值写回表单并视为「已修改」。 */
  applyValues(values, { asBaseline = false } = {}) {
    this.values = Object.assign({}, this.values, deepCopy(values) || {});
    if (asBaseline) this.baseline = deepCopy(this.values);
    this.serverErrors.clear();
    this.writeAll();
  }

  getValues() { return this.values; }

  getBaseline() { return this.baseline; }

  getField(path) { return this.byPath.get(path) || null; }

  fieldLabel(path) {
    const field = this.byPath.get(path);
    return field ? field.label : path;
  }

  /* ---------------- 脏检查 ---------------- */

  isChanged(path) {
    return !sameValue(this.values[path], this.baseline[path]);
  }

  dirtyPaths() {
    return this.fields.map((f) => f.path).filter((path) => this.isChanged(path));
  }

  isDirty() { return this.dirtyPaths().length > 0; }

  changedCount() { return this.dirtyPaths().length; }

  resetAll() {
    this.values = deepCopy(this.baseline);
    this.serverErrors.clear();
    this.writeAll();
    this.notify();
  }

  resetPath(path) {
    this.values[path] = deepCopy(this.baseline[path]);
    this.serverErrors.delete(path);
    const row = this.rows.get(path);
    if (row) row.write(this.values[path]);
    this.refreshRow(path);
    this.notify();
  }

  /** 客户端整体校验：返回 {path: message} */
  errors() {
    const result = {};
    this.fields.forEach((field) => {
      const message = validateField(field, this.values[field.path]);
      if (message) result[field.path] = message;
    });
    return result;
  }

  markServerErrors(errors) {
    this.serverErrors.clear();
    (errors || []).forEach((item) => {
      if (item && item.path) this.serverErrors.set(item.path, item.message);
    });
    this.fields.forEach((field) => this.refreshRow(field.path));
    const first = (errors || []).find((item) => item && item.path);
    if (first) this.scrollTo(first.path);
  }

  scrollTo(path) {
    const row = this.rows.get(path);
    if (!row) return;
    const details = row.node.closest("details");
    if (details) details.open = true;
    row.node.classList.add("flash");
    row.node.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => row.node.classList.remove("flash"), 1600);
  }

  /* ---------------- 城市候选与城市相关提示 ---------------- */

  /** 用当前已知的城市清单刷新所有「城市」下拉候选（app 解析出城市后调用）。 */
  refreshCityOptions() {
    const options = (this.handlers.cityOptions ? this.handlers.cityOptions() : []) || [];
    this.cityDatalists.forEach((datalist) => {
      datalist.innerHTML = "";
      options.forEach((item) => {
        datalist.appendChild(h("option", {
          value: item.display,
          label: `${item.cityId}　${item.memName || ""}`.trim(),
        }));
      });
    });
  }

  /** 重算城市相关控件的行内提示（切换出图模式 / 城市集合变化后调用）。 */
  refreshCityHints() {
    this.cityHintRefreshers.forEach((fn) => fn());
  }

  /* ---------------- 过滤 ---------------- */

  setKeyword(keyword) {
    this.keyword = (keyword || "").trim().toLowerCase();
    this.applyFilters();
  }

  setOnlyChanged(flag) {
    this.onlyChanged = Boolean(flag);
    this.applyFilters();
  }

  applyFilters() {
    this.groups.forEach((group) => {
      const container = this.groupNodes.get(group.key);
      if (!container) return;
      // blocks = 主区 + 各二级小节 + 各三级小节，逐块过滤（避免主区字段被漏掉）
      const blocks = container.blocks || [{ details: null, rows: container.rows }];
      const visibleRows = new Map();
      let visibleTotal = 0;
      blocks.forEach((block) => {
        let shown = 0;
        block.rows.forEach((row) => {
          const field = row.field;
          const haystack = `${field.label} ${field.path} ${field.desc}`.toLowerCase();
          const matchKeyword = !this.keyword || haystack.includes(this.keyword);
          const matchChanged = !this.onlyChanged || this.isChanged(field.path);
          const show = matchKeyword && matchChanged;
          row.node.classList.toggle("hidden", !show);
          visibleRows.set(row, show);
          if (show) shown += 1;
        });
        if (block.details) {
          block.details.classList.toggle("hidden", shown === 0);
          if (this.keyword && shown > 0) block.details.open = true;
        }
        visibleTotal += shown;
      });
      // 二级小节自身：直属行 + 其下三级小节，全部不可见时才隐藏
      (container.subgroups || []).forEach((sub) => {
        const countOf = (rows) => rows.reduce((n, row) => n + (visibleRows.get(row) ? 1 : 0), 0);
        let shown = countOf(sub.rows);
        sub.families.forEach((fam) => { shown += countOf(fam.rows); });
        sub.details.classList.toggle("hidden", shown === 0);
      });
      container.details.classList.toggle("hidden", visibleTotal === 0);
      if (this.keyword && visibleTotal > 0) container.details.open = true;
      const counter = container.countNode;
      if (counter) {
        counter.textContent = this.keyword || this.onlyChanged
          ? `${visibleTotal} / ${container.rows.length} 项`
          : `${container.rows.length} 项`;
      }
    });
  }

  /* ---------------- 构建 DOM ---------------- */

  build() {
    this.root.innerHTML = "";
    this.rows.clear();
    this.groupNodes = new Map();
    this.cityDatalists = [];
    this.cityHintRefreshers = [];

    this.groups.forEach((group) => {
      const body = h("div", { class: "group-body" });
      const counter = h("span", { class: "group-count" });
      const details = h("details", { class: "group", open: group.key === "data" }, [
        h("summary", {}, [
          h("span", { text: group.title }),
          // 说明在标题栏里单行省略，完整文案放 title，悬停可看
          h("span", { class: "group-desc", text: group.desc || "", title: group.desc || "" }),
          counter,
        ]),
        body,
      ]);
      this.root.appendChild(details);

      const container = {
        details, rows: [], subgroups: [], mainRows: [], countNode: counter,
      };
      this.groupNodes.set(group.key, container);

      const subgroupDefs = (group.subgroups && group.subgroups.length)
        ? group.subgroups
        : (group.key === "series" && group.seriesKeys ? group.seriesKeys : []);
      if (subgroupDefs.length) {
        // 二级分组：绘图元素（series）/ 各对象小节（标题、图例、量程…）
        container.subgroups = [];
        const mainBody = h("div", {});
        body.appendChild(mainBody);        // 不属于子分组的字段仍排在前面
        const rowsByKey = new Map();
        subgroupDefs.forEach((item, index) => {
          const subBody = h("div", { class: "group-body" });
          const subDetails = h("details", {
            // 展开大类时顺手展开第一个小节，避免「点开还要再点一层」
            class: "group",
            open: index === 0 || item.value === "maxTemp" || item.value === "basic",
          }, [
            h("summary", {}, [h("span", { text: item.label })]),
            subBody,
          ]);
          body.appendChild(subDetails);
          const sub = { details: subDetails, body: subBody, rows: [], families: new Map() };
          container.subgroups.push(sub);
          rowsByKey.set(item.value, sub);
        });
        (group.fields || []).forEach((field) => {
          const raw = field.subgroup || field.path.split(".")[1];
          const [top, family] = String(raw).split("|");
          const sub = rowsByKey.get(top);
          const row = this.buildRow(field);
          if (!sub) {
            mainBody.appendChild(row.node);
            container.mainRows.push(row);
          } else if (family) {
            // 三级：元素内再按「基础 / 线与标记 / 柱状 / 数值标签」折叠
            let fam = sub.families.get(family);
            if (!fam) {
              const famBody = h("div", { class: "group-body" });
              const famDetails = h("details", { class: "group", open: family === "basic" }, [
                h("summary", {}, [h("span", { text: field.subgroupLabel || family })]),
                famBody,
              ]);
              sub.body.appendChild(famDetails);
              fam = { details: famDetails, body: famBody, rows: [] };
              sub.families.set(family, fam);
            }
            fam.body.appendChild(row.node);
            fam.rows.push(row);
          } else {
            sub.body.appendChild(row.node);
            sub.rows.push(row);
          }
          container.rows.push(row);
        });
        // 过滤与计数按「块」进行：主区 / 各二级小节 / 各三级小节
        container.blocks = [{ details: null, rows: container.mainRows }];
        container.subgroups.forEach((sub) => {
          container.blocks.push({ details: sub.details, rows: sub.rows });
          sub.families.forEach((fam) => {
            container.blocks.push({ details: fam.details, rows: fam.rows });
          });
        });
      } else {
        (group.fields || []).forEach((field) => {
          const row = this.buildRow(field);
          body.appendChild(row.node);
          container.rows.push(row);
        });
      }
    });

    function subBodyOf(sub) {
      return sub.details.querySelector(".group-body");
    }
  }

  buildRow(field) {
    const control = this.buildControl(field);
    const message = h("div", { class: "fmsg" });
    const actions = h("div", { class: "factions" });
    const name = h("div", { class: "fname" }, [h("span", { text: field.label })]);
    if (field.extra) name.appendChild(h("span", { class: "badge warn small", text: "扩展项" }));
    if (field.readonly) name.appendChild(h("span", { class: "badge small", text: "只读" }));

    const meta = h("div", { class: "fmeta" }, [
      name,
      h("div", { class: "fpath", text: field.path }),
      field.desc ? h("div", { class: "fdesc", text: field.desc }) : null,
    ]);

    const resetButton = h("button", {
      class: "ghost small",
      text: "还原",
      title: "还原为加载时的取值",
      onclick: () => this.resetPath(field.path),
    });
    actions.appendChild(resetButton);

    const node = h("div", { class: "frow" }, [
      meta,
      h("div", { class: "fcontrol" }, [control.node, message, actions]),
    ]);

    const row = {
      field, node, read: control.read, write: control.write, message, actions,
    };
    this.rows.set(field.path, row);
    return row;
  }

  /** 依据数据类型构造控件。 */
  buildControl(field) {
    const kind = field.kind;
    const commit = () => {
      const value = control.read();
      this.values[field.path] = value;
      this.serverErrors.delete(field.path);
      this.refreshRow(field.path);
      this.notify();
    };
    const control = {};

    const numberInput = (step) => h("input", {
      type: "number", value: "", step: step || "any", class: "numbox",
      disabled: field.readonly,
      oninput: commit, onchange: commit,
    });
    const textInput = (extra) => h("input", Object.assign({
      type: "text", value: "", disabled: field.readonly,
      placeholder: field.placeholder || "",
      oninput: commit, onchange: commit,
    }, extra || {}));

    if (field.readonly) {
      control.node = h("div", { class: "control-line" }, [textInput()]);
      control.read = () => (control.node.querySelector("input").value);
      control.write = (value) => { control.node.querySelector("input").value = value === null ? "" : value; };
      return control;
    }

    switch (kind) {
      case "bool": {
        const box = h("input", { type: "checkbox", onchange: commit });
        control.node = h("label", { class: "field checkbox" }, [box, h("span", { text: "启用 / 是" })]);
        control.read = () => box.checked;
        control.write = (value) => { box.checked = Boolean(value); };
        break;
      }

      case "bool_or_null": {
        // 三态：跟随上层（null）/ 开 / 关
        const select = h("select", { onchange: commit });
        select.appendChild(h("option", { value: "", text: field.nullLabel || "跟随上层设置" }));
        select.appendChild(h("option", { value: "true", text: "开（true）" }));
        select.appendChild(h("option", { value: "false", text: "关（false）" }));
        control.node = h("div", { class: "control-line" }, [select]);
        control.read = () => (select.value === "" ? null : select.value === "true");
        control.write = (value) => {
          select.value = value === null || value === undefined ? "" : String(Boolean(value));
        };
        break;
      }

      case "int":
      case "float": {
        const input = numberInput(field.step || (kind === "int" ? 1 : "any"));
        const line = h("div", { class: "control-line" }, [input]);
        if (field.unit) line.appendChild(h("span", { class: "muted small", text: field.unit }));
        control.node = line;
        // 不做四舍五入：带小数的 int 原样交给校验去报错，避免静默改值
        control.read = () => {
          const raw = input.value.trim();
          if (raw === "") return null;
          const number = Number(raw);
          return Number.isFinite(number) ? number : raw;
        };
        control.write = (value) => { input.value = value === null || value === undefined ? "" : value; };
        break;
      }

      case "int_or_null":
      case "num_or_null": {
        const input = numberInput(field.step || (kind === "int_or_null" ? 1 : "any"));
        const line = h("div", { class: "control-line" }, [
          input, h("span", { class: "muted small", text: field.unit || "" }),
          h("span", { class: "null-toggle", text: "留空 = 自动" }),
        ]);
        control.node = line;
        control.read = () => {
          const raw = input.value.trim();
          if (raw === "") return null;
          const number = Number(raw);
          return Number.isFinite(number) ? number : raw;
        };
        control.write = (value) => { input.value = value === null || value === undefined ? "" : value; };
        break;
      }

      case "num2":
      case "num2_or_null": {
        const first = numberInput("any");
        const second = numberInput("any");
        const line = h("div", { class: "control-line" }, [
          // 两个数字框作为一组：始终并排、等比收缩，空间不够时让提示文字换行
          h("div", { class: "num-pair" }, [first, second]),
          h("span", { class: "muted small",
            text: kind === "num2_or_null" ? "两项都留空 = 自动" : "如 [12, 6]" }),
        ]);
        control.node = line;
        control.read = () => {
          const a = first.value.trim();
          const b = second.value.trim();
          if (a === "" && b === "") return kind === "num2_or_null" ? null : [];
          return [a === "" ? null : Number(a), b === "" ? null : Number(b)];
        };
        control.write = (value) => {
          const list = Array.isArray(value) ? value : [];
          first.value = list.length > 0 && list[0] !== null ? list[0] : "";
          second.value = list.length > 1 && list[1] !== null ? list[1] : "";
        };
        break;
      }

      case "enum_or_null": {
        const select = h("select", { onchange: commit });
        select.appendChild(h("option", { value: "", text: field.nullLabel || "（自动）" }));
        field.enum.forEach((item) => {
          select.appendChild(h("option", { value: String(item.value), text: item.label || String(item.value) }));
        });
        control.node = h("div", { class: "control-line" }, [select]);
        control.read = () => {
          if (select.value === "") return null;
          const option = field.enum.find((item) => String(item.value) === select.value);
          return option ? option.value : select.value;
        };
        control.write = (value) => {
          const text = value === null || value === undefined ? "" : String(value);
          if (text && !field.enum.some((item) => String(item.value) === text)) {
            select.appendChild(h("option", { value: text, text: `${text}（当前值，不在候选内）` }));
          }
          select.value = text;
        };
        break;
      }

      case "enum": {
        const select = h("select", { onchange: commit });
        field.enum.forEach((item) => {
          select.appendChild(h("option", { value: String(item.value), text: item.label || String(item.value) }));
        });
        control.node = h("div", { class: "control-line" }, [select]);
        control.read = () => {
          const option = field.enum.find((item) => String(item.value) === select.value);
          if (!option) return select.value;
          return field.coerce === "int" ? Number(option.value) : option.value;
        };
        control.write = (value) => {
          const text = value === null || value === undefined ? "" : String(value);
          if (!field.enum.some((item) => String(item.value) === text)) {
            select.appendChild(h("option", { value: text, text: `${text}（当前值，不在候选内）` }));
          }
          select.value = text;
        };
        break;
      }

      case "color": {
        const text = textInput({ class: "color-text" });
        const picker = h("input", {
          type: "color", value: "#ffffff",
          oninput: () => { text.value = picker.value; commit(); },
        });
        const line = h("div", { class: "control-line" }, [picker, text]);
        field.specials.forEach((special) => {
          line.appendChild(h("button", {
            class: "ghost small", text: special,
            onclick: () => { text.value = special; commit(); },
          }));
        });
        control.node = line;
        control.read = () => text.value.trim();
        control.write = (value) => {
          const content = value === null || value === undefined ? "" : String(value);
          text.value = content;
          if (HEX_RE.test(content) && (content.length === 7 || content.length === 4)) {
            picker.value = content.length === 4
              ? `#${content[1]}${content[1]}${content[2]}${content[2]}${content[3]}${content[3]}`
              : content;
          }
        };
        break;
      }

      case "rotation":
      case "grid":
      case "str":
      case "path": {
        const listId = `dl-${field.path.replace(/\./g, "-")}`;
        // 角度字段的候选是 textOptions（vertical / horizontal），其余用 suggestions
        const candidates = kind === "rotation"
          ? (field.textOptions || [])
          : (field.suggestions || []);
        const input = textInput({ list: candidates.length ? listId : null });
        control.node = h("div", { class: "control-line" }, [input]);
        if (candidates.length) {
          const datalist = h("datalist", { id: listId });
          candidates.forEach((item) => datalist.appendChild(h("option", { value: item })));
          control.node.appendChild(datalist);
        }
        if (kind === "grid") input.placeholder = "auto / 2x2 / 3x2";
        control.read = () => {
          const raw = input.value;
          if (kind !== "rotation") return raw;
          // 角度：数字要读回数字，否则会与基线类型不一致而恒显示「已修改」
          const text = raw.trim();
          if (text === "" || field.textOptions.includes(text)) return text;
          const number = Number(text);
          return Number.isFinite(number) ? number : text;
        };
        control.write = (value) => { input.value = value === null || value === undefined ? "" : String(value); };
        break;
      }

      case "template": {
        const input = textInput({ class: "tag-input" });
        const hint = h("div", { class: "muted small",
          text: `可用占位符：${(field.placeholders || []).map((p) => `{${p}}`).join(" ")}` });
        control.node = h("div", { class: "control-line" }, [input, hint]);
        control.read = () => input.value;
        control.write = (value) => { input.value = value === null || value === undefined ? "" : String(value); };
        break;
      }

      case "list_enum": {
        const boxes = field.options.map((item) => {
          const box = h("input", { type: "checkbox", value: String(item.value) });
          const label = h("label", {}, [box, h("span", { text: item.label || String(item.value) })]);
          return { box, label, value: item.value };
        });
        const hint = h("span", { class: "muted small" });
        const refreshLimit = () => {
          const picked = boxes.filter((item) => item.box.checked).length;
          if (field.maxItems) {
            // 选满后禁用未勾选项，避免「点了才报错」
            boxes.forEach((item) => {
              item.box.disabled = !item.box.checked && picked >= field.maxItems;
            });
            hint.textContent = picked >= field.maxItems
              ? `最多 ${field.maxItems} 项（已选满）`
              : `最多可选 ${field.maxItems} 项（已选 ${picked}）`;
          } else if (field.allowEmpty) {
            hint.textContent = "都不选 = 不输出";
          } else {
            hint.textContent = "";
          }
        };
        boxes.forEach((item) => {
          item.box.addEventListener("change", () => { refreshLimit(); commit(); });
        });
        control.node = h("div", { class: "checkbox-grid" }, boxes.map((item) => item.label));
        control.node.appendChild(hint);
        control.read = () => {
          const selected = boxes.filter((item) => item.box.checked).map((item) => item.value);
          // 勾选集合没变时保留原有顺序，避免"只是重新渲染就显示已修改"
          const previous = this.values[field.path];
          if (Array.isArray(previous) && previous.length === selected.length
              && previous.every((item) => selected.some((one) => String(one) === String(item)))) {
            return previous.slice();
          }
          return selected;
        };
        control.write = (value) => {
          const list = (Array.isArray(value) ? value : []).map((item) => String(item));
          boxes.forEach((item) => { item.box.checked = list.includes(String(item.value)); });
          refreshLimit();
        };
        break;
      }

      case "list_str":
      case "list_color": {
        const input = textInput({ class: "tag-input",
          placeholder: kind === "list_color" ? "#d85a30, #185fa5" : "逗号分隔，如 a, b" });
        control.node = h("div", { class: "control-line" }, [input,
          h("span", { class: "muted small", text: "逗号分隔" })]);
        control.read = () => input.value.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
        control.write = (value) => {
          input.value = Array.isArray(value) ? value.join(", ") : "";
        };
        break;
      }

      case "str_map": {
        if (field.keyKind === "city" || field.valueKind === "color") {
          Object.assign(control, this.buildCityColorMap(field, commit));
          break;
        }
        const table = h("div", { class: "kv-table" });
        const addButton = h("button", { class: "ghost small", text: "＋ 添加一项" });
        const suggestionId = `kv-${field.path.replace(/\./g, "-")}`;
        const datalist = h("datalist", { id: suggestionId });
        (field.suggestions || []).forEach((item) => datalist.appendChild(h("option", { value: item })));

        const addRow = (key, value) => {
          const keyInput = h("input", { type: "text", class: "kv-key", value: key,
            list: (field.suggestions || []).length ? suggestionId : null, oninput: commit });
          const valueInput = h("input", { type: "text", value,
            oninput: commit });
          const remove = h("button", {
            class: "ghost small", text: "删除",
            onclick: () => { row.remove(); commit(); },
          });
          const row = h("div", { class: "kv-row" }, [keyInput, valueInput, remove]);
          table.appendChild(row);
          return row;
        };

        control.node = h("div", {}, [table, h("div", { class: "control-line" }, [addButton, datalist])]);
        addButton.onclick = () => { addRow("", ""); commit(); };
        control.read = () => {
          const result = {};
          $$(".kv-row", table).forEach((row) => {
            const inputs = $$("input", row);
            const key = inputs[0].value.trim();
            if (key) result[key] = inputs[1].value;
          });
          return result;
        };
        control.write = (value) => {
          table.innerHTML = "";
          const entries = isPlainObject(value) ? Object.entries(value) : [];
          entries.forEach(([key, item]) => addRow(key, item === null ? "" : String(item)));
          if (entries.length === 0) addRow("", "");
        };
        break;
      }

      case "json": {
        const area = h("textarea", { class: "json-box", oninput: commit });
        control.node = h("div", { class: "control-line" }, [area]);
        control.read = () => {
          const raw = area.value.trim();
          if (raw === "") return [];
          try {
            return JSON.parse(raw);
          } catch (error) {
            return raw;
          }
        };
        control.write = (value) => {
          area.value = value === undefined || value === null
            ? "" : JSON.stringify(value, null, 2);
        };
        break;
      }

      default: {
        const input = textInput();
        control.node = h("div", { class: "control-line" }, [input]);
        control.read = () => input.value;
        control.write = (value) => { input.value = value === null || value === undefined ? "" : String(value); };
      }
    }

    return control;
  }

  /* ---------------- 「城市 → 颜色」专用键值表 ---------------- */

  /**
   * 为 ``compare.color_map`` 这类「键是城市、值是颜色」的映射生成专用控件：
   * 城市可从下拉选（候选来自实际解析出的城市显示名）、颜色可从下拉选预设色，
   * 还能一键为当前对比城市各钉一个固定颜色。
   */
  buildCityColorMap(field, commit) {
    const slug = field.path.replace(/\./g, "-");
    const cityListId = `dl-city-${slug}`;
    const colorListId = `dl-color-${slug}`;
    const cityDatalist = h("datalist", { id: cityListId });
    const colorDatalist = h("datalist", { id: colorListId });
    const valueOptions = field.valueOptions || [];
    valueOptions.forEach((item) => {
      colorDatalist.appendChild(h("option", { value: item.value, label: item.label }));
    });
    this.cityDatalists.push(cityDatalist);

    const presetColors = valueOptions.map((item) => item.value);
    const itemsBox = h("div", { class: "kv-table" });
    const notice = h("div", { class: "fmsg warn" });
    const summary = h("div", { class: "muted small" });

    const rows = () => $$(".cc-item", itemsBox);
    const readAll = () => {
      const result = {};
      rows().forEach((item) => {
        const key = $(".cc-key", item).value.trim();
        if (key) result[key] = $(".cc-value", item).value.trim();
      });
      return result;
    };
    const colorNameOf = (value) => {
      const hit = valueOptions.find(
        (item) => String(item.value).toLowerCase() === String(value).toLowerCase());
      return hit ? hit.label : "";
    };
    const syncPicker = (item) => {
      const text = $(".cc-value", item).value.trim();
      if (HEX_RE.test(text) && (text.length === 7 || text.length === 4)) {
        $(".cc-pick", item).value = text.length === 4
          ? `#${text[1]}${text[1]}${text[2]}${text[2]}${text[3]}${text[3]}` : text;
      }
    };
    const compareCities = () => (
      (this.handlers.compareCities ? this.handlers.compareCities() : []) || []);

    const refreshHints = () => {
      const known = compareCities();
      const inCompare = known.length > 0;
      const byCityId = String(this.values["compare.color_by"] || "") === "city_id";
      notice.textContent = byCityId
        ? "「取色依据」当前是 city_id：本表不会生效（取色改为 colors[cityId % 长度]）。"
          + "要按城市固定颜色，请把「取色依据」改为 order。"
        : "";
      notice.classList.toggle("hidden", !notice.textContent);

      let missing = 0;
      rows().forEach((item) => {
        const key = $(".cc-key", item).value.trim();
        const color = $(".cc-value", item).value.trim();
        const hint = $(".cc-hint", item);
        let message = "";
        let level = "";
        if (key) {
          if (!color) {
            message = "未填颜色，该城市会退回轮询色";
            level = "warn";
          } else if (!isColorValue(color)) {
            message = "不是合法颜色：请用 #RRGGBB / 颜色名，或从下拉里选";
            level = "err";
          } else if (inCompare && !known.includes(key)) {
            message = `不在本次对比城市中（${known.join("、")}），绘图时该项会被忽略`;
            level = "warn";
            missing += 1;
          } else {
            message = colorNameOf(color) || "";
          }
        }
        hint.textContent = message;
        hint.className = `fmsg cc-hint ${level}`;
        item.classList.toggle("invalid", level === "err");
        item.classList.toggle("warn", level === "warn");
      });

      const total = Object.keys(readAll()).length;
      summary.textContent = inCompare
        ? `本次对比城市：${known.join("、")}｜已指定 ${total} 个颜色`
          + (missing ? `，其中 ${missing} 个城市不在本次对比中（会被忽略）` : "")
        : "只在「多城市对比」模式下生效；单城 / 多图模式会忽略本表。"
          + "填入城市显示名即可固定其颜色，避免换对比元素或增删城市后颜色漂移。";
    };
    this.cityHintRefreshers.push(refreshHints);

    const afterChange = () => {
      refreshHints();
      commit();
    };

    const addRow = (key, value) => {
      const keyInput = h("input", {
        type: "text", class: "cc-key kv-key", value: key, list: cityListId,
        placeholder: "城市显示名（可下拉选）",
      });
      const picker = h("input", { type: "color", class: "cc-pick", value: "#d85a30" });
      const valueInput = h("input", {
        type: "text", class: "cc-value kv-value tag-input", value,
        list: colorListId, placeholder: "#RRGGBB",
      });
      const hint = h("div", { class: "fmsg cc-hint" });
      const remove = h("button", { class: "ghost small", text: "删除" });
      const item = h("div", { class: "cc-item" }, [
        h("div", { class: "kv-row" }, [keyInput, picker, valueInput, remove]),
        hint,
      ]);

      remove.onclick = () => { item.remove(); afterChange(); };
      keyInput.addEventListener("change", () => {
        // 选了城市但还没填颜色 → 自动给一个尚未用过的预设色
        if (keyInput.value.trim() && !valueInput.value.trim()) {
          const used = Object.values(readAll());
          valueInput.value = presetColors.find((color) => !used.includes(color))
            || presetColors[used.length % Math.max(1, presetColors.length)]
            || "#d85a30";
          syncPicker(item);
        }
        afterChange();
      });
      keyInput.addEventListener("input", afterChange);
      picker.oninput = () => { valueInput.value = picker.value; afterChange(); };
      valueInput.addEventListener("input", () => { syncPicker(item); afterChange(); });

      itemsBox.appendChild(item);
      return item;
    };

    const write = (value) => {
      const entries = isPlainObject(value) ? Object.entries(value) : [];
      itemsBox.innerHTML = "";
      if (entries.length) {
        entries.forEach(([key, color]) => addRow(key, color === null ? "" : String(color)));
      } else {
        addRow("", "");
      }
      rows().forEach(syncPicker);
      refreshHints();
    };

    /** 一键配色用的调色板：优先用当前配置里的「城市配色序列」。 */
    const paletteOf = () => {
      const configured = (this.values["compare.colors"] || [])
        .filter((color) => typeof color === "string" && HEX_RE.test(color.trim()))
        .map((color) => color.trim());
      return configured.length ? configured : presetColors;
    };

    const addButton = h("button", {
      class: "ghost small", text: "＋ 添加城市",
      onclick: () => { addRow("", ""); afterChange(); },
    });
    const fillButton = h("button", {
      class: "ghost small", text: "按当前对比城市一键钉色",
      title: "给本次对比的每个城市各分配一个固定颜色（之后不再随排序、换元素、增删城市而变化）",
      onclick: () => {
        const known = compareCities();
        if (!known.length) {
          toast("还没解析出对比城市：请先在上方填城市、切到「多城市对比」并点「反查编号」", "warn");
          return;
        }
        const palette = paletteOf();
        const next = readAll();
        known.forEach((name, index) => {
          if (!next[name]) next[name] = palette[index % palette.length];
        });
        write(next);
        afterChange();
        toast(`已为 ${Object.keys(next).length} 个城市指定颜色（尚未保存）`, "ok");
      },
    });
    const clearButton = h("button", {
      class: "ghost small", text: "清空",
      onclick: () => { write({}); afterChange(); },
    });

    const node = h("div", { class: "control-line city-color" }, [
      h("div", { class: "cc-head" }, [addButton, fillButton, clearButton]),
      notice,
      itemsBox,
      summary,
      cityDatalist,
      colorDatalist,
    ]);

    return { node, read: readAll, write, refreshHints };
  }

  /* ---------------- 刷新 ---------------- */

  writeAll() {
    this.fields.forEach((field) => {
      const row = this.rows.get(field.path);
      if (!row) return;
      row.write(this.values[field.path]);
    });
    this.fields.forEach((field) => this.refreshRow(field.path));
    this.applyFilters();
  }

  refreshRow(path) {
    const row = this.rows.get(path);
    if (!row) return;
    const changed = this.isChanged(path);
    row.node.classList.toggle("changed", changed);
    row.actions.classList.toggle("hidden", !changed);

    let message = this.serverErrors.get(path) || "";
    if (!message && kind_isJsonInvalid(row.field, this.values[path])) {
      message = "JSON 解析失败，请检查括号与引号";
    }
    if (!message) message = validateField(row.field, this.values[path]) || "";
    row.message.textContent = message;
    row.message.className = `fmsg ${message ? "err" : ""}`;
    row.node.classList.toggle("invalid", Boolean(message));
  }

  notify() {
    // 只有开启「只看已修改」时才需要重算过滤（避免每次按键遍历全部配置项）
    if (this.onlyChanged) this.applyFilters();
    // 城市颜色表的提示依赖「取色依据」等字段，随改动一起刷新（只遍历该表自身的行，很轻）
    if (this.cityHintRefreshers.length) this.refreshCityHints();
    if (this.handlers.onChange) this.handlers.onChange(this);
    if (this.handlers.onDirtyChange) {
      this.handlers.onDirtyChange(this.isDirty(), this.changedCount());
    }
  }
}

function kind_isJsonInvalid(field, value) {
  return field.kind === "json" && typeof value === "string";
}
