"use strict";

/* =========================================================================
 * 应用编排：配置装载 / 保存与另存 / 出图 / 实时日志 / 右侧显示区
 * ========================================================================= */

/* ---------------- 左右分栏 ---------------- */

// 左栏宽度按**窗口宽度**夹紧：不小于 1/3、不大于 1/2
const SPLIT_MIN_RATIO = 1 / 3;
const SPLIT_MAX_RATIO = 1 / 2;
const SPLIT_DEFAULT_RATIO = 0.48;

const state = {
  schema: null,
  profiles: [],
  defaultProfile: "default",
  profile: null,
  detail: null,
  warnings: [],
  taskId: null,
  cursor: 0,
  logs: [],
  artifacts: [],
  selectedChart: null,
  selectedTable: null,
  source: null,
  polling: false,
  done: true,
  autoScroll: true,
  logLevels: new Set(["debug", "info", "warning", "error"]),
  cityCandidates: [],      // 已解析 / 查询过的城市（供「指定城市颜色」的键下拉）
  targetCities: [],        // 本次对比城市的显示名（仅「多城市对比」模式有效）
  resolveTimer: null,
  lastProfile: "",         // 上次使用的配置（服务端持久化，刷新/重开自动选中）
  leftWidthRatio: 0,       // 左栏占窗口宽度的比例（0 = 用默认值）
};

let form = null;

/* ---------------- 启动 ---------------- */

async function init() {
  form = new ConfigForm($("#config-groups"), {
    onChange: () => updateConfigSummary(),
    onDirtyChange: (dirty, count) => {
      const badge = $("#dirty-badge");
      badge.classList.toggle("hidden", !dirty);
      badge.textContent = `● ${count} 项修改未保存`;
      updateConfigSummary();
    },
    // 「指定城市颜色」的键候选 / 当前对比城市：只有多城对比时后者才非空
    cityOptions: () => state.cityCandidates,
    compareCities: () => (currentMode() === "compare" ? state.targetCities : []),
  });

  wireEvents();

  try {
    const [schema, profiles, saved] = await Promise.all([
      API.get("/api/schema"),
      API.get("/api/profiles"),
      // 上次使用的配置（拿不到就当没有，不影响启动）
      API.get("/api/state").catch(() => ({ lastProfile: "" })),
    ]);
    state.schema = schema;
    state.profiles = profiles.profiles || [];
    state.defaultProfile = profiles.defaultProfile || schema.defaultProfile || "default";
    state.lastProfile = String(saved.lastProfile || "");
    applySplitRatio(saved.leftRatio || SPLIT_DEFAULT_RATIO);   // 恢复上次的左右宽度
    form.setSchema(schema);
    fillProfileSelect();

    const names = state.profiles.map((item) => item.name);
    const fallback = names.includes(state.defaultProfile)
      ? state.defaultProfile
      : (names[0] || "default");
    const preferred = pickInitialProfile(saved, fallback);
    const loaded = await loadInitialProfile(preferred, fallback);
    if (loaded !== fallback) {
      toast(`已自动选中上次使用的配置「${loaded}」（默认配置为「${fallback}」）`, "ok");
    } else {
      toast("已就绪：填入城市后点「出图」", "ok");
    }
  } catch (error) {
    toast(`初始化失败：${error.message}`, "err");
  }
}

/**
 * 初始配置。服务端已在 `/api/state` 里算好 `profile`（上次使用且仍存在 → 否则默认），
 * 这里再按当前配置清单兜一层底，避免两次请求之间清单发生变化。
 */
function pickInitialProfile(saved, fallback) {
  const names = state.profiles.map((item) => item.name);
  const candidates = [saved && saved.profile, saved && saved.lastProfile, fallback, "default"];
  for (const name of candidates) {
    const text = String(name || "").trim();
    if (text && names.includes(text)) return text;
  }
  return names[0] || "default";
}

/** 载入初始配置：首选加载失败（如配置被改坏）时回退到默认配置。 */
async function loadInitialProfile(preferred, fallback) {
  try {
    await loadProfile(preferred);
    return preferred;
  } catch (error) {
    if (preferred === fallback) throw error;
    const message = error.payload ? error.payload.error : error.message;
    toast(`上次使用的配置「${preferred}」加载失败（${message}），已改用默认配置`, "warn", 9000);
    await loadProfile(fallback);
    return fallback;
  }
}

/** 记住「上次使用的配置」；失败不影响使用（只是下次不会自动选中）。 */
function rememberProfile(name) {
  if (!name || name === state.lastProfile) return;
  state.lastProfile = name;
  API.post("/api/state", { lastProfile: name }).catch(() => {});
}

/* ---------------- 左右分栏拖动 ---------------- */

/** 夹紧到窗口宽度的 1/3 ~ 1/2。 */
function clampSplitRatio(ratio) {
  const value = Number(ratio);
  if (!Number.isFinite(value) || value <= 0) return SPLIT_DEFAULT_RATIO;
  return Math.min(SPLIT_MAX_RATIO, Math.max(SPLIT_MIN_RATIO, value));
}

/** 应用左栏宽度（以窗口宽度为基准）。返回实际采用的占比。 */
function applySplitRatio(ratio, { persist = false } = {}) {
  const clamped = clampSplitRatio(ratio);
  const width = Math.round(window.innerWidth * clamped);
  document.documentElement.style.setProperty("--left-width", `${width}px`);
  state.leftWidthRatio = clamped;
  if (persist) {
    API.post("/api/state", { leftRatio: clamped }).catch(() => {});
  }
  return clamped;
}

function initSplitter() {
  const splitter = $("#splitter");
  const layout = $(".layout");
  if (!splitter || !layout) return;

  const ratioFromPointer = (event) => {
    const rect = layout.getBoundingClientRect();
    return (event.clientX - rect.left) / window.innerWidth;
  };
  const endDrag = () => {
    splitter.classList.remove("dragging");
    document.body.classList.remove("splitting");
    applySplitRatio(state.leftWidthRatio, { persist: true });   // 只在拖完后记一次
  };

  splitter.addEventListener("pointerdown", (event) => {
    if (event.button !== undefined && event.button !== 0) return;   // 只响应左键
    event.preventDefault();
    splitter.classList.add("dragging");
    document.body.classList.add("splitting");
    if (splitter.setPointerCapture) splitter.setPointerCapture(event.pointerId);

    const onMove = (moveEvent) => applySplitRatio(ratioFromPointer(moveEvent));
    const onUp = () => {
      splitter.removeEventListener("pointermove", onMove);
      splitter.removeEventListener("pointerup", onUp);
      splitter.removeEventListener("pointercancel", onUp);
      endDrag();
    };
    splitter.addEventListener("pointermove", onMove);
    splitter.addEventListener("pointerup", onUp);
    splitter.addEventListener("pointercancel", onUp);
  });

  // 双击复位；← → 微调（Shift 加速）
  splitter.addEventListener("dblclick", () => {
    applySplitRatio(SPLIT_DEFAULT_RATIO, { persist: true });
  });
  splitter.addEventListener("keydown", (event) => {
    const step = event.shiftKey ? 0.05 : 0.01;
    if (event.key === "ArrowLeft") {
      applySplitRatio(state.leftWidthRatio - step, { persist: true });
    } else if (event.key === "ArrowRight") {
      applySplitRatio(state.leftWidthRatio + step, { persist: true });
    } else {
      return;
    }
    event.preventDefault();
  });

  // 窗口尺寸变化：按同一比例重新夹紧（防抖）
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(
      () => applySplitRatio(state.leftWidthRatio || SPLIT_DEFAULT_RATIO), 120);
  });

  applySplitRatio(state.leftWidthRatio || SPLIT_DEFAULT_RATIO);
}

function fillProfileSelect() {
  const select = $("#profile-select");
  select.innerHTML = "";
  state.profiles.forEach((item) => {
    const isDefault = item.name === state.defaultProfile;
    const suffix = item.shadowed ? "（自定义覆盖内置）"
      : item.builtin ? "（内置）" : "（自定义）";
    select.appendChild(h("option", {
      value: item.name,
      text: `${item.name}${isDefault ? " · 默认" : ""}${suffix}`,
    }));
  });
}

/* ---------------- 配置装载 ---------------- */

async function loadProfile(name) {
  const detail = await API.get(`/api/profiles/${encodeURIComponent(name)}`);
  // 服务端对「找不到 / 无法解析」的配置会回退到内置默认，这里如实告知，
  // 避免用户以为自己在编辑 A、实际编辑的是默认配置。
  if (name && detail.name && detail.name !== name) {
    toast(`配置「${name}」无法加载（可能已被删除或文件有误），已回退到「${detail.name}」`,
      "warn", 9000);
  }
  state.profile = detail.name;
  state.detail = detail;
  state.warnings = [];
  form.setValues(detail.values);
  $("#profile-select").value = detail.name;
  updateProfileMeta();
  updateConfigSummary();
  renderWarnings();
  clearResult();
  rememberProfile(detail.name);     // 供下次刷新 / 重开自动选中
}

function updateProfileMeta() {
  const detail = state.detail;
  if (!detail) return;
  const parts = [`来源：${detail.source}`];
  if (detail.extends) parts.push(`继承：${detail.extends}`);
  if (detail.applyStyles && detail.applyStyles.length) {
    parts.push(`样式：${detail.applyStyles.join("、")}`);
  }
  if (detail.unknownKeys && detail.unknownKeys.length) {
    parts.push(`含 ${detail.unknownKeys.length} 个表单外键（需手工编辑文件）`);
  }
  $("#profile-source").textContent = parts.join("　|　");
}

function updateConfigSummary() {
  const total = form.fields.length;
  const changed = form.changedCount();
  $("#config-summary").textContent = changed
    ? `${total} 项，已修改 ${changed} 项`
    : `${total} 项，未修改`;
}

/* ---------------- 事件绑定 ---------------- */

function wireEvents() {
  initSplitter();

  $("#profile-select").addEventListener("change", async (event) => {
    const target = event.target.value;
    if (target === state.profile) return;
    const ok = await guardDirty(() => loadProfile(target));
    if (!ok) event.target.value = state.profile;
  });

  $("#config-search").addEventListener("input", (event) => form.setKeyword(event.target.value));
  $("#only-changed").addEventListener("change", (event) => form.setOnlyChanged(event.target.checked));
  $("#btn-reset-all").addEventListener("click", async () => {
    if (!form.isDirty()) { toast("没有需要还原的修改", "warn"); return; }
    const yes = await showModal({
      title: "全部还原",
      body: h("div", { text: "将丢弃当前全部未保存修改，还原为加载时的取值。" }),
      buttons: [{ label: "取消", value: false }, { label: "还原", kind: "primary", value: true }],
    });
    if (yes) { form.resetAll(); toast("已还原", "ok"); }
  });

  $("#btn-save").addEventListener("click", () => saveCurrent());
  $("#btn-save-as").addEventListener("click", () => saveAsDialog());
  $("#btn-marks").addEventListener("click", () => marksDialog());
  $("#btn-final").addEventListener("click", () => textDialog("final"));
  $("#btn-template").addEventListener("click", () => textDialog("template"));
  $("#btn-cache").addEventListener("click", () => cacheDialog());
  $("#btn-run").addEventListener("click", () => render());
  $("#btn-cancel").addEventListener("click", () => cancelTask());

  $$("#mode-row input[name=mode]").forEach((radio) => {
    radio.addEventListener("change", () => {
      updateModeUi();
      if (currentMode() === "compare") {
        resolveTargetCities({ silent: true }).catch(() => {});
      } else {
        state.targetCities = [];
        form.refreshCityHints();
      }
    });
  });

  // 对比元素最多 4 个：勾满后禁用未选项（与配置表单里的上限一致）
  const metricBoxes = $$("#metric-checks input");
  const refreshMetricLimit = () => {
    const picked = metricBoxes.filter((box) => box.checked).length;
    metricBoxes.forEach((box) => { box.disabled = !box.checked && picked >= 4; });
    const hint = $("#metric-hint");
    // 未选时不占位（保持「标签 + 元素」同一行），选了才显示计数
    if (!picked) {
      hint.textContent = "";
      hint.classList.add("hidden");
      return;
    }
    hint.classList.remove("hidden");
    hint.textContent = picked >= 4 ? `已选 ${picked} / 4（已满）` : `已选 ${picked} / 4`;
  };
  metricBoxes.forEach((box) => box.addEventListener("change", refreshMetricLimit));
  refreshMetricLimit();

  // 城市输入变化时（防抖）解析出显示名，供「指定城市颜色」用
  $("#city-input").addEventListener("input", () => scheduleResolve());
  $("#city-input").addEventListener("change", () => scheduleResolve({ silent: false }));

  $("#btn-resolve").addEventListener("click", () => resolveNames());
  $("#btn-search-city").addEventListener("click", () => citySearchDialog());

  $("#btn-fit").addEventListener("click", () => {
    $("#chart-stage").classList.toggle("fitted");
  });
  $("#btn-download-chart").addEventListener("click", () => downloadSelectedChart());

  $$("#result-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  $("#log-autoscroll").addEventListener("change", (event) => {
    state.autoScroll = event.target.checked;
    if (state.autoScroll) scrollLogs();
  });
  $$(".log-level").forEach((box) => {
    box.addEventListener("change", () => {
      if (box.checked) state.logLevels.add(box.value);
      else state.logLevels.delete(box.value);
      renderLogs();
    });
  });
  $("#btn-clear-log").addEventListener("click", () => { state.logs = []; renderLogs(); });

  window.addEventListener("beforeunload", (event) => {
    if (form.isDirty()) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
}

function updateModeUi() {
  const mode = currentMode();
  // 对比模式也要排版输入：多元素 facet 用它做分面布局（写入 compare.grid）
  $("#grid-field").classList.toggle("hidden", mode !== "multi" && mode !== "compare");
  $("#metric-field").classList.toggle("hidden", mode !== "compare");
  $("#compare-mode-field").classList.toggle("hidden", mode !== "compare");
  $("#mode-hint").textContent = {
    city: "每个城市一张统计图 + 一份表格",
    compare: "多城市对比（至少 2 个城市）；可多选元素做分面 / 叠加",
    multi: "多城市画在同一张画布（至少 2 个城市）",
  }[mode];
  $("#city-input").placeholder = mode === "multi"
    ? "cityId 或城市名，逗号分隔，如：北京,香港,莫斯科,伦敦"
    : "cityId 或城市名，逗号分隔，如：237,北京,香港";
  if (form) form.refreshCityHints();
}

/* ---------------- 目标城市解析 ---------------- */

function scheduleResolve({ silent = true } = {}) {
  if (state.resolveTimer) clearTimeout(state.resolveTimer);
  state.resolveTimer = setTimeout(() => {
    state.resolveTimer = null;
    resolveTargetCities({ silent }).catch(() => {});
  }, 500);
}

/**
 * 把「目标城市」里的每一项（cityId 或城市名）解析成城市，拿到**显示名**。
 * 显示名正是 `compare.color_map` 需要的键，也是对比城市校验的依据。
 */
async function resolveTargetCities({ silent = true } = {}) {
  const cities = parseCities($("#city-input").value);
  if (!cities.length) {
    state.targetCities = [];
    form.refreshCityHints();
    return [];
  }
  const query = `/api/cities?resolve=${encodeURIComponent(cities.join(","))}`
    + `&profile=${encodeURIComponent(state.profile || "")}`;
  const payload = await API.get(query);
  const resolved = payload.resolved || [];
  const found = resolved.filter((item) => item.ok);
  state.targetCities = found.map((item) => item.display);
  found.forEach((item) => {
    if (!state.cityCandidates.some((one) => one.cityId === item.cityId)) {
      state.cityCandidates.push(item);
    }
  });
  state.cityCandidates.sort((a, b) => a.cityId - b.cityId);
  form.refreshCityOptions();
  form.refreshCityHints();
  if (!silent) {
    const missed = resolved.filter((item) => !item.ok);
    if (missed.length) {
      toast(`未找到匹配城市：${missed.map((item) => item.input).join("、")}`, "warn");
    }
  }
  return resolved;
}

function currentMode() {
  const checked = $("#mode-row input[name=mode]:checked");
  return checked ? checked.value : "city";
}

function parseCities(text) {
  const items = [];
  String(text || "").split(/[,\n\r]+/).forEach((piece) => {
    const value = piece.trim();
    if (value && !items.includes(value)) items.push(value);
  });
  return items;
}

/* ---------------- 脏检查守卫 ---------------- */

async function guardDirty(action) {
  if (!form.isDirty()) {
    if (action) await action();
    return true;
  }
  const choice = await showModal({
    title: "配置已修改，是否保存？",
    body: h("div", {}, [
      h("p", { text: `已修改 ${form.changedCount()} 个配置项（当前配置：${state.profile}）。` }),
      h("p", { class: "muted small", text: "「保存修改」写入 config/custom/ 下的同名配置文件；「另存为」可另起名称并选择保存方式。" }),
    ]),
    buttons: [
      { label: "取消", value: "cancel" },
      { label: "放弃修改", value: "discard" },
      { label: "另存为…", value: "saveas" },
      { label: "保存修改", kind: "primary", value: "save" },
    ],
  });

  if (choice === "cancel" || choice === undefined) return false;
  if (choice === "discard") {
    form.resetAll();
    if (action) await action();
    return true;
  }
  if (choice === "save") {
    const ok = await saveCurrent({ silent: true });
    if (!ok) return false;
    if (action) await action();
    return true;
  }
  if (choice === "saveas") {
    const saved = await saveAsDialog();
    if (!saved) return false;
    if (action) await action();
    return true;
  }
  return false;
}

/* ---------------- 保存与另存 ---------------- */

/** 表单里的「配置说明」——保存 / 另存时说明文案的唯一来源。 */
function formDescription() {
  const value = form.getValues()["profile_description"];
  return typeof value === "string" ? value : "";
}

/** 保存成功后的统一提示（含「原文件注释会丢失」的提醒）。 */
function reportSaved(saved, { silent = false } = {}) {
  if (!silent) toast(`已保存：${saved.path}`, "ok");
  if (saved.lostComments > 0) {
    toast(`原文件里的 ${saved.lostComments} 行注释在保存后丢失（YAML 回写不保留注释）`, "warn", 9000);
  }
}

async function saveCurrent({ silent = false } = {}) {
  const detail = state.detail || {};
  const builtin = detail.builtin || detail.shadowed;
  const description = formDescription();
  if (builtin) {
    const yes = await showModal({
      title: "该配置与内置配置同名",
      body: h("div", {}, [
        h("p", { text: `「${state.profile}」在内置 config/profiles.yaml 中也有定义。` }),
        h("p", { class: "muted small",
          text: "内置文件不会被改动；本次保存会写入 config/custom/" + state.profile
              + ".yaml，按加载顺序覆盖内置同名配置。" }),
      ]),
      buttons: [{ label: "取消", value: false }, { label: "继续保存", kind: "primary", value: true }],
    });
    if (!yes) return false;
  }

  try {
    const payload = {
      name: state.profile,
      mode: "keep",
      base: state.profile,
      description,
      values: form.getValues(),
    };
    const result = await API.post("/api/profiles/save", payload);
    await refreshProfiles();
    await loadProfile(result.saved.name);
    reportSaved(result.saved, { silent });
    return true;
  } catch (error) {
    if (error.payload && error.payload.error) {
      toast(`保存失败：${error.payload.error}`, "err");
    } else {
      toast(`保存失败：${error.message}`, "err");
    }
    return false;
  }
}

async function saveAsDialog() {
  const nameInput = h("input", { type: "text", value: `${state.profile}_副本` });
  const descInput = h("input", {
    type: "text",
    value: formDescription() || (state.detail ? state.detail.description : ""),
  });
  const modeFull = h("input", { type: "radio", name: "savemode", value: "full", checked: true });
  const modeExtends = h("input", { type: "radio", name: "savemode", value: "extends" });
  const errorLine = h("div", { class: "fmsg err" });

  const body = h("div", {}, [
    h("label", { class: "field" }, [h("span", { class: "label", text: "新配置名（同时作为文件名）" }), nameInput]),
    h("label", { class: "field" }, [h("span", { class: "label", text: "说明文案（可选）" }), descInput]),
    h("div", { class: "field" }, [
      h("span", { class: "label", text: "另存方式" }),
      h("div", { class: "radio-cards" }, [
        h("label", { class: "radio-card" }, [modeFull, h("div", {}, [
          h("div", { class: "rc-title", text: "保存全量配置" }),
          h("div", { class: "rc-desc", text: "把当前生效的全部配置项写成一份独立配置，不依赖其他配置。" }),
        ])]),
        h("label", { class: "radio-card" }, [modeExtends, h("div", {}, [
          h("div", { class: "rc-title", text: "继承后仅存修改部分" }),
          h("div", { class: "rc-desc",
            text: `写成「extends: ${state.profile}」加差异项；基线配置以后调整时，本配置会跟着变。` }),
        ])]),
      ]),
    ]),
    h("p", { class: "muted small", text: "保存位置：config/custom/<新配置名>.yaml" }),
    errorLine,
  ]);

  const result = await showModal({
    title: "另存为新配置",
    body,
    buttons: [
      { label: "取消", value: false },
      {
        label: "确认另存",
        kind: "primary",
        action: () => {
          const name = nameInput.value.trim();
          if (!name) { errorLine.textContent = "配置名不能为空"; return false; }
          if (/[\\/:*?"<>|]/.test(name)) {
            errorLine.textContent = '配置名不能包含 \\ / : * ? " < > |';
            return false;
          }
          const exists = state.profiles.some((item) => item.name === name);
          if (exists && !window.confirm(`配置「${name}」已存在，确认覆盖？`)) return false;
          errorLine.textContent = "";
          return { name, description: descInput.value };
        },
      },
    ],
  });
  if (!result) return false;

  const mode = modeExtends.checked ? "extends" : "full";
  try {
    const saved = await API.post("/api/profiles/save", {
      name: result.name,
      mode,
      base: state.profile,
      description: result.description,
      values: form.getValues(),
    });
    await refreshProfiles();
    await loadProfile(saved.saved.name);
    toast(`已另存为「${saved.saved.name}」（${mode === "full" ? "全量" : "继承差量"}）→ ${saved.saved.path}`, "ok");
    if (saved.saved.lostComments > 0) {
      toast(`原文件里的 ${saved.saved.lostComments} 行注释在覆盖后丢失（YAML 回写不保留注释）`, "warn", 9000);
    }
    return true;
  } catch (error) {
    toast(`另存失败：${error.payload ? error.payload.error : error.message}`, "err");
    return false;
  }
}

async function refreshProfiles() {
  const payload = await API.get("/api/profiles");
  state.profiles = payload.profiles || [];
  fillProfileSelect();
}

/* ---------------- 工具弹窗 ---------------- */

const MARKS_HELP = [
  "h / hh / hhh：气温轴上下限同时 +10 / +20 / +30",
  "c / cc / ccc：同 h 但反向（-10 / -20 / -30）",
  "r<数字>：降水轴上限档位 0-5 = 50/100/150/300/600/900mm（r12 = 250）",
  "t<数字> / p<数字> / tt<数字>：气温轴 / 降水轴 / 图表标题字号（tt0 = 不显示标题）",
  "ts<数字> / rs<数字>：气温轴 / 降水轴刻度步长",
  "cy：城市名取短名 / cn：城市名用完整名称",
].join("\n");

async function marksDialog() {
  const input = h("input", { type: "text", placeholder: "如：hh,r12,t14,p14,tt18" });
  const body = h("div", {}, [
    h("p", { class: "muted small", text: "逗号分隔，可一次给多个；空格会自动去除。" }),
    h("label", { class: "field" }, [h("span", { class: "label", text: "微调标记" }), input]),
    h("pre", { text: MARKS_HELP }),
  ]);
  const marks = await showModal({
    title: "快捷微调（--mark 语法）",
    body,
    buttons: [{ label: "取消", value: null },
      { label: "应用", kind: "primary", action: () => input.value }],
  });
  if (marks === null || marks === undefined || marks === "") return;
  try {
    const result = await API.post("/api/profiles/marks", {
      profile: state.profile, values: form.getValues(), marks,
    });
    form.applyValues(result.values);
    (result.notes || []).forEach((note) => toast(note, "ok"));
    (result.warnings || []).forEach((note) => toast(`微调告警：${note}`, "warn"));
    if (!result.notes.length && !result.warnings.length) toast("该标记未产生变化", "warn");
    toast("微调结果已写入表单（未保存）", "warn");
  } catch (error) {
    toast(`微调失败：${error.payload ? error.payload.error : error.message}`, "err");
  }
}

async function textDialog(kind) {
  try {
    const payload = await API.post("/api/profiles/text", {
      profile: state.profile, values: form.getValues(), kind,
    });
    const title = kind === "template" ? "全量配置模板（导出为 YAML）" : "最终配置（当前表单值套用后）";
    const fileName = `${state.profile}_${kind === "template" ? "template" : "final"}.yaml`;
    await showTextModal(title, payload.text, fileName);
  } catch (error) {
    toast(`获取失败：${error.payload ? error.payload.error : error.message}`, "err");
  }
}

async function cacheDialog() {
  const statsBox = h("div", { class: "muted small" });
  const refresh = async () => {
    const stats = await API.get("/api/cache");
    statsBox.innerHTML = "";
    stats.items.forEach((item) => {
      statsBox.appendChild(h("div", {
        text: `${item.label}：${item.exists ? `${item.files} 个文件 / ${formatBytes(item.bytes)}` : "目录不存在"}　${item.path}`,
      }));
    });
    if (stats.cityIndex) {
      statsBox.appendChild(h("div", {
        text: `城市索引缓存：${formatBytes(stats.cityIndex.bytes)}　${stats.cityIndex.path}`,
      }));
    }
  };
  await refresh();

  await showModal({
    title: "数据缓存",
    body: h("div", {}, [
      h("p", { class: "muted small",
        text: "缓存命中时不会发起网络请求、也不占限速额度。清理后下次出图会重新请求数据源。" }),
      statsBox,
    ]),
    buttons: [
      { label: "关闭", value: "close" },
      {
        label: "清理响应缓存",
        action: async () => {
          const result = await API.post("/api/cache/clear", { index: false, raw: false });
          toast(`已删除 ${result.removed} 个缓存文件`, "ok");
          await refresh();
          return false;
        },
      },
      {
        label: "全部清理（含城市索引）",
        kind: "primary",
        action: async () => {
          const result = await API.post("/api/cache/clear", { index: true, raw: true });
          toast(`已删除 ${result.removed} 个文件（含城市索引与原始响应）`, "ok");
          await refresh();
          return false;
        },
      },
    ],
  });
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

/* ---------------- 城市查询 ---------------- */

async function resolveNames() {
  const cities = parseCities($("#city-input").value);
  const box = $("#city-resolved");
  box.innerHTML = "";
  if (!cities.length) {
    box.textContent = "请先填写城市。";
    return;
  }
  box.textContent = "正在反查…";
  try {
    const resolved = await resolveTargetCities({ silent: true });
    box.innerHTML = "";
    if (!resolved.length) {
      box.textContent = "没有可反查的城市。";
      return;
    }
    box.appendChild(h("div", { class: "muted small",
      text: "点击结果可把 cityId 填入输入框；解析出的显示名已加入「指定城市颜色」的候选：" }));
    resolved.forEach((item) => {
      if (!item.ok) {
        box.appendChild(h("div", { class: "muted small", text: `${item.input} → 未找到匹配城市` }));
        return;
      }
      box.appendChild(h("div", {}, [h("button", {
        class: "ghost small",
        text: `${item.input} → ${item.cityId} ${item.display}`
          + `（${item.memName}${item.coords ? " " + item.coords : ""}）`,
        onclick: () => appendCity(item.cityId),
      })]));
    });
  } catch (error) {
    box.textContent = `反查失败：${error.payload ? error.payload.error : error.message}`;
  }
}

async function citySearchDialog() {
  const input = h("input", { type: "text", placeholder: "城市名关键词，如：北京 / Tokyo" });
  const countryInput = h("input", { type: "text", placeholder: "国家/地区（可选），如：中国" });
  const listBox = h("div", {});
  const status = h("div", { class: "muted small" });

  const doSearch = async () => {
    status.textContent = "查询中…";
    listBox.innerHTML = "";
    try {
      const payload = await API.get(
        `/api/cities?q=${encodeURIComponent(input.value.trim())}`
        + `&country=${encodeURIComponent(countryInput.value.trim())}&limit=80`);
      status.textContent = `共 ${payload.total} 条（最多显示 ${payload.limit} 条）`;
      const table = h("table", { class: "preview" }, [
        h("thead", {}, [h("tr", {}, [
          h("th", { text: "cityId" }), h("th", { text: "城市" }),
          h("th", { text: "国家/地区" }), h("th", { text: "坐标" }), h("th", { text: "操作" }),
        ])]),
      ]);
      const tbody = h("tbody", {});
      payload.cities.forEach((city) => {
        tbody.appendChild(h("tr", {}, [
          h("td", { text: city.cityId }),
          h("td", { text: city.display }),
          h("td", { text: city.memName }),
          h("td", { text: city.coords || "" }),
          h("td", {}, [h("button", {
            class: "ghost small", text: "填入",
            onclick: () => {
              appendCity(city.cityId);
              rememberCity(city);
              status.textContent = `已填入 ${city.cityId}（显示名「${city.display}」已加入颜色候选）`;
            },
          })]),
        ]));
      });
      table.appendChild(tbody);
      listBox.appendChild(table);
    } catch (error) {
      status.textContent = `查询失败：${error.payload ? error.payload.error : error.message}`;
    }
  };

  await showModal({
    title: "查询城市编号",
    body: h("div", {}, [
      h("div", { class: "inline-fields" }, [
        h("label", { class: "field" }, [h("span", { class: "label", text: "关键词" }), input]),
        h("label", { class: "field" }, [h("span", { class: "label", text: "国家/地区" }), countryInput]),
      ]),
      h("div", { class: "control-line" }, [h("button", { class: "primary small", text: "查询", onclick: doSearch })]),
      status,
      listBox,
    ]),
    buttons: [{ label: "关闭", kind: "primary" }],
  });
  doSearch();
}

function appendCity(cityId) {
  const input = $("#city-input");
  const cities = parseCities(input.value);
  if (!cities.includes(String(cityId))) cities.push(String(cityId));
  input.value = cities.join(", ");
  scheduleResolve();
}

/** 记住一个城市，作为「指定城市颜色」键下拉的候选。 */
function rememberCity(city) {
  if (!city || !city.cityId) return;
  if (state.cityCandidates.some((item) => item.cityId === city.cityId)) return;
  state.cityCandidates.push({
    cityId: city.cityId,
    name: city.name || "",
    display: city.display || city.name || "",
    memName: city.memName || "",
    coords: city.coords || "",
  });
  state.cityCandidates.sort((a, b) => a.cityId - b.cityId);
  form.refreshCityOptions();
  form.refreshCityHints();
}

/* ---------------- 出图 ---------------- */

async function render() {
  if (!state.done) { toast("已有任务在运行，请先等待或取消", "warn"); return; }
  const cities = parseCities($("#city-input").value);
  if (!cities.length) { toast("请先填写城市（cityId 或城市名）", "warn"); return; }

  const mode = currentMode();
  if (mode !== "city" && cities.length < 2) {
    toast(mode === "compare" ? "多城市对比至少需要 2 个城市" : "多图至少需要 2 个城市", "warn");
    return;
  }
  if (!(await guardDirty())) return;

  const localErrors = form.errors();
  if (Object.keys(localErrors).length) {
    const errors = Object.entries(localErrors).map(([path, message]) => ({ path, message }));
    form.markServerErrors(errors);
    // 本地校验的明细也要进「提示」页签，否则只弹出条数、看不到是哪一项
    state.warnings = errors;
    renderWarnings();
    switchTab("warn");
    toast(`有 ${errors.length} 个配置项填写有误：${errors[0].path}（${errors[0].message}）`, "err");
    return;
  }

  // 对比模式：先解析出城市显示名，`compare.color_map` 的键校验要以它为准
  if (mode === "compare") {
    try {
      await resolveTargetCities({ silent: true });
    } catch (error) {
      toast(`城市解析失败（不影响出图）：${error.payload ? error.payload.error : error.message}`, "warn");
    }
  }
  const compareCities = mode === "compare" ? state.targetCities : [];

  let checked;
  try {
    checked = await API.post("/api/profiles/validate", {
      profile: state.profile,
      values: form.getValues(),
      mode,
      compare_cities: compareCities,
    });
  } catch (error) {
    toast(`校验失败：${error.payload ? error.payload.error : error.message}`, "err");
    return;
  }
  // 逐字段错误已就地标红；没有具体字段的跨字段错误（如「未启用任何绘图元素」）放进「提示」
  const crossErrors = (checked.errors || []).filter((item) => !item.path);
  state.warnings = [...(checked.warnings || []), ...crossErrors];
  renderWarnings();
  if (!checked.ok) {
    form.markServerErrors(checked.errors);
    switchTab("warn");
    toast("配置校验未通过，请查看「提示」页签", "err");
    return;
  }
  form.applyValues(checked.normalized);

  const body = {
    mode,
    cities,
    profile: state.profile,
    values: checked.normalized,
    save_files: !$("#no-save-toggle").checked,
    refresh_cache: $("#refresh-cache").checked,
    refresh_index: $("#refresh-index").checked,
    grid: $("#grid-input").value.trim(),
    compare_metrics: $$("#metric-checks input:checked").map((box) => box.value),
    compare_mode: $("#compare-mode").value,
    compare_cities: compareCities,
  };

  clearResult();
  setRunning(true);
  switchTab("log");
  pushLog({
    i: -1, t: 0, level: "info",
    message: `提交任务：${mode === "city" ? "单城/批量" : mode === "compare" ? "对比图" : "多图"}`
      + ` · 城市 ${cities.join(", ")} · 配置 ${state.profile}`
      + ` · ${body.save_files ? "保存到本地" : "仅网页显示（不保存）"}`,
  });

  try {
    const payload = await API.post("/api/render", body);
    startTask(payload.task);
  } catch (error) {
    setRunning(false);
    const errors = (error.payload && error.payload.errors) || [];
    const detail = errors.length
      ? errors.map((item) => `${item.path} ${item.message}`).join("；")
      : null;
    toast(`出图失败：${error.payload ? (detail || error.payload.error) : error.message}`, "err");
    if (errors.length) {
      form.markServerErrors(errors);
      state.warnings = [...(error.payload.warnings || []), ...errors.filter((i) => !i.path)];
      renderWarnings();
      switchTab("warn");
    }
  }
}

function startTask(task) {
  state.taskId = task.id;
  state.cursor = 0;
  state.logs = [];
  state.artifacts = [];
  state.done = false;
  state.polling = false;
  $("#result-status").classList.remove("hidden");
  $("#result-status").className = "badge run";
  $("#result-status").textContent = "运行中";
  $("#btn-cancel").classList.remove("hidden");
  renderLogs();
  absorb(task);
  openStream();
}

function openStream() {
  if (!window.EventSource) { startPolling(); return; }
  try {
    state.source = new EventSource(`/api/render/${state.taskId}/events?cursor=${state.cursor}`);
  } catch (error) {
    startPolling();
    return;
  }
  state.source.onmessage = (event) => {
    let payload;
    try { payload = JSON.parse(event.data); } catch (error) { return; }
    if (payload.type === "ping") return;
    absorb(payload);
  };
  state.source.onerror = () => {
    if (state.done) { closeStream(); return; }
    closeStream();
    startPolling();
  };
}

function closeStream() {
  if (state.source) { state.source.close(); state.source = null; }
}

async function startPolling() {
  if (state.polling || state.done) return;
  state.polling = true;
  while (!state.done) {
    let snapshot;
    try {
      snapshot = await API.get(`/api/render/${state.taskId}?cursor=${state.cursor}&wait=20`);
    } catch (error) {
      state.polling = false;
      toast(`日志流中断：${error.payload ? error.payload.error : error.message}`, "err");
      return;
    }
    absorb(snapshot);
  }
  state.polling = false;
}

/** 合并一次任务快照：日志、产物、结果。 */
function absorb(snapshot) {
  if (!snapshot) return;
  if (Array.isArray(snapshot.logs)) {
    snapshot.logs.forEach((entry) => pushLog(entry));
  }
  if (typeof snapshot.cursor === "number") state.cursor = snapshot.cursor;

  if (Array.isArray(snapshot.artifacts) && snapshot.artifacts.length !== state.artifacts.length) {
    state.artifacts = snapshot.artifacts;
    renderArtifacts();
  }
  if (snapshot.result && Object.keys(snapshot.result).length) {
    renderResult(snapshot.result);
  }
  const status = snapshot.status;
  if (status === "done" || status === "error" || status === "cancelled") {
    finishTask(status, snapshot.result || {});
  }
}

function finishTask(status, result) {
  state.done = true;
  closeStream();
  setRunning(false);
  $("#btn-cancel").classList.add("hidden");
  const badge = $("#result-status");
  badge.classList.remove("hidden");
  badge.className = "badge " + (status === "done" ? "ok" : status === "cancelled" ? "warn" : "err");
  badge.textContent = status === "done" ? "完成"
    : status === "cancelled" ? "已取消" : "失败";

  if (status === "done") {
    const saved = state.artifacts.filter((item) => item.saved).length;
    toast(`出图完成：${result.summary || ""}${saved ? `；已保存 ${saved} 个文件到 output` : "（未写入本地）"}`,
      "ok");
  } else if (status === "cancelled") {
    toast("任务已取消", "warn");
  } else {
    toast("任务失败，请查看运行日志", "err");
    switchTab("log");
  }
  if (state.artifacts.some((item) => item.kind === "chart")) {
    switchTab("chart");
  }
}

function setRunning(running) {
  state.done = !running;
  $("#btn-run").disabled = running;
  $("#btn-cancel").classList.toggle("hidden", !running);
}

async function cancelTask() {
  if (!state.taskId || state.done) return;
  try {
    await API.post(`/api/render/${state.taskId}/cancel`, {});
    toast("已请求取消", "warn");
  } catch (error) {
    toast(`取消失败：${error.message}`, "err");
  }
}

/* ---------------- 日志 ---------------- */

function pushLog(entry) {
  state.logs.push(entry);
  if (state.logs.length > 4000) state.logs.splice(0, state.logs.length - 4000);
  if (state.logLevels.has(entry.level)) {
    const stream = $("#log-stream");
    stream.appendChild(logLine(entry));
    if (state.autoScroll) scrollLogs();
  }
}

function logLine(entry) {
  const seconds = Number(entry.t || 0).toFixed(2).padStart(6, " ");
  return h("div", { class: `log-line ${entry.level}` }, [
    h("span", { class: "t", text: `${seconds}s` }),
    h("span", { class: "lv", text: entry.level.toUpperCase() }),
    h("span", { class: "msg", text: entry.message }),
  ]);
}

function renderLogs() {
  const stream = $("#log-stream");
  stream.innerHTML = "";
  state.logs.filter((entry) => state.logLevels.has(entry.level))
    .forEach((entry) => stream.appendChild(logLine(entry)));
  if (state.autoScroll) scrollLogs();
}

function scrollLogs() {
  const stream = $("#log-stream");
  stream.scrollTop = stream.scrollHeight;
}

/* ---------------- 提示 ---------------- */

function renderWarnings() {
  const list = $("#warn-list");
  list.innerHTML = "";
  const count = state.warnings.length;
  const badge = $("#warn-count");
  badge.classList.toggle("hidden", count === 0);
  badge.textContent = count;

  if (!count) {
    list.appendChild(h("div", { class: "warn-item ok", text: "当前配置没有告警。" }));
    return;
  }
  state.warnings.forEach((item) => {
    list.appendChild(h("div", { class: "warn-item" }, [
      item.path ? h("div", { class: "wpath", text: item.path }) : null,
      h("div", { text: item.message }),
    ]));
  });
}

function clearWarnings() {
  state.warnings = [];
  renderWarnings();
}

/* ---------------- 右侧显示区 ---------------- */

function clearResult() {
  state.artifacts = [];
  state.selectedChart = null;
  state.selectedTable = null;
  $("#chart-strip").innerHTML = "";
  $("#table-picker").innerHTML = "";
  $("#chart-stage").innerHTML = "";
  $("#chart-stage").appendChild(h("div", { class: "empty" }, [
    h("p", { text: "还没有图。" }),
    h("p", { class: "muted", text: "在左侧填入城市，点右上角「出图」即可即时显示。" }),
  ]));
  $("#table-stage").innerHTML = "";
  $("#table-stage").appendChild(h("div", { class: "empty" }, [h("p", { class: "muted", text: "还没有表格。" })]));
  $("#chart-meta").textContent = "";
  $("#btn-download-chart").disabled = true;
  $("#result-status").classList.add("hidden");
}

function renderArtifacts() {
  const charts = state.artifacts.filter((item) => item.kind === "chart");
  const tables = state.artifacts.filter((item) => item.kind === "table");

  const strip = $("#chart-strip");
  strip.innerHTML = "";
  if (!charts.length) {
    strip.appendChild(h("span", { class: "muted small", text: "本次没有图片产物。" }));
  }
  charts.forEach((artifact) => {
    const button = h("button", {
      class: "ghost",
      text: `${artifact.label || artifact.name} · ${artifact.fmt.toUpperCase()}`
        + (artifact.saved ? " ·已保存" : " ·未保存"),
      onclick: () => selectChart(artifact.id),
    });
    button.dataset.id = artifact.id;
    strip.appendChild(button);
  });
  if (charts.length) selectChart(charts[0].id);

  const picker = $("#table-picker");
  picker.innerHTML = "";
  if (!tables.length) {
    picker.appendChild(h("span", { class: "muted small", text: "本次没有表格产物。" }));
  }
  tables.forEach((artifact) => {
    const button = h("button", {
      class: "ghost",
      text: `${artifact.city || ""} ${artifact.fmt.toUpperCase()}`.trim()
        + (artifact.saved ? " ·已保存" : " ·未保存"),
      onclick: () => selectTable(artifact.id),
    });
    button.dataset.id = artifact.id;
    picker.appendChild(button);
  });
  if (tables.length) selectTable(tables[0].id);
}

function selectChart(id) {
  const artifact = state.artifacts.find((item) => item.id === id);
  if (!artifact) return;
  state.selectedChart = artifact;
  $$("#chart-strip button").forEach((button) => {
    button.classList.toggle("active", button.dataset.id === id);
  });
  const stage = $("#chart-stage");
  stage.innerHTML = "";
  stage.appendChild(h("img", { src: artifact.url, alt: artifact.label || artifact.name }));
  $("#chart-meta").textContent = [
    artifact.label || "",
    `格式 ${artifact.fmt.toUpperCase()}`,
    `大小 ${formatBytes(artifact.size)}`,
    artifact.saved ? `已保存：${artifact.path}` : "未写入本地（可用「另存本地图片」下载）",
  ].filter(Boolean).join("　|　");
  $("#btn-download-chart").disabled = false;
}

function downloadSelectedChart() {
  const artifact = state.selectedChart;
  if (!artifact) return;
  const link = h("a", { href: `${artifact.url}?download=1`, download: artifact.name });
  document.body.appendChild(link);
  link.click();
  link.remove();
  toast(`已开始下载 ${artifact.name}`, "ok");
}

function selectTable(id) {
  const artifact = state.artifacts.find((item) => item.id === id);
  if (!artifact) return;
  state.selectedTable = artifact;
  $$("#table-picker button").forEach((button) => {
    button.classList.toggle("active", button.dataset.id === id);
  });
  const stage = $("#table-stage");
  stage.innerHTML = "";
  stage.appendChild(h("div", { class: "control-line" }, [
    h("a", { class: "ghost small", href: `${artifact.url}?download=1`, download: artifact.name },
      [`下载 ${artifact.name}`]),
    h("span", { class: "muted small",
      text: artifact.saved ? `已保存：${artifact.path}` : "未写入本地" }),
  ]));

  if (artifact.fmt === "xlsx") {
    stage.appendChild(h("p", { class: "muted", text: "Excel 表格不支持网页预览，请点上方按钮下载。" }));
    return;
  }
  fetch(artifact.url).then((response) => response.text()).then((text) => {
    if (artifact.fmt === "csv") {
      stage.appendChild(renderCsv(text));
    } else {
      stage.appendChild(h("pre", { class: "preview", text }));
    }
  }).catch((error) => {
    stage.appendChild(h("p", { class: "muted", text: `预览失败：${error.message}` }));
  });
}

/** 极简 CSV 解析（支持引号与转义） + 表格渲染。 */
function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') { cell += '"'; index += 1; } else { quoted = false; }
      } else {
        cell += char;
      }
      continue;
    }
    if (char === '"') { quoted = true; continue; }
    if (char === ",") { row.push(cell); cell = ""; continue; }
    if (char === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; continue; }
    if (char === "\r") continue;
    cell += char;
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

function renderCsv(text) {
  const rows = parseCsv(text.replace(/^\ufeff/, "")).filter((row) => row.some((c) => c !== ""));
  const width = Math.max(1, ...rows.map((row) => row.length));
  const table = h("table", { class: "preview" });
  rows.forEach((row) => {
    if (row.length === 1) {
      table.appendChild(h("tr", {}, [h("td", {
        colspan: width, class: "muted small", text: row[0],
      })]));
      return;
    }
    table.appendChild(h("tr", {}, row.map((cell) => h("td", { text: cell }))));
  });
  return table;
}

function switchTab(name) {
  $$("#result-tabs .tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  $$(".tab-pane").forEach((pane) => pane.classList.toggle("active", pane.dataset.pane === name));
  if (name === "log" && state.autoScroll) scrollLogs();
}

function renderResult(result) {
  // 结果摘要写入运行日志即可（图表/表格已由 renderArtifacts 渲染）
  if (result.summary) {
    $("#chart-meta").textContent = $("#chart-meta").textContent || result.summary;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  init();
  updateModeUi();
});
