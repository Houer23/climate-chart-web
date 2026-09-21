"use strict";

/* =========================================================================
 * 极简前端基础设施：API 客户端 + DOM 助手 + 提示 / 弹窗
 * 无任何第三方依赖、无构建步骤。
 * ========================================================================= */

class ApiError extends Error {
  constructor(status, payload, path) {
    const message = (payload && payload.error) || `HTTP ${status}`;
    super(message);
    this.status = status;
    this.payload = payload || {};
    this.path = path;
  }
}

const API = {
  async request(method, path, body) {
    const options = { method, headers: {} };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, options);
    } catch (error) {
      throw new ApiError(0, { error: `无法连接服务：${error.message}` }, path);
    }
    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (error) {
        payload = { error: text.slice(0, 500) };
      }
    }
    if (!response.ok) throw new ApiError(response.status, payload, path);
    return payload;
  },
  get(path) { return this.request("GET", path); },
  post(path, body) { return this.request("POST", path, body); },
};

/* ---------------- DOM 助手 ---------------- */

const $ = (selector, root) => (root || document).querySelector(selector);
const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));

/** 极简元素构造：h("div", {class: "x"}, [子元素或文本]) */
function h(tag, props, children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key === "checked" || key === "disabled" || key === "selected") {
        node[key] = true;
      } else if (key === "value") {
        node.value = value === null ? "" : value;
      } else {
        node.setAttribute(key, value);
      }
    }
  }
  append(node, children);
  return node;
}

function append(node, children) {
  if (children === null || children === undefined) return;
  if (Array.isArray(children)) {
    children.forEach((child) => append(node, child));
  } else if (children instanceof Node) {
    node.appendChild(children);
  } else {
    node.appendChild(document.createTextNode(String(children)));
  }
}

const deepCopy = (value) => (value === undefined ? undefined : JSON.parse(JSON.stringify(value)));
const sameValue = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/* ---------------- 提示 ---------------- */

function toast(message, kind, timeout) {
  const root = $("#toast-root");
  const node = h("div", { class: `toast ${kind || ""}`, text: message });
  root.appendChild(node);
  setTimeout(() => node.remove(), timeout || (kind === "err" ? 8000 : 3600));
}

/* ---------------- 弹窗 ---------------- */

/**
 * 通用弹窗。options:
 *   title, body(Node|string), buttons:[{label, kind, value, action}], onClose
 * 返回 Promise<value>。
 */
function showModal(options) {
  return new Promise((resolve) => {
    const root = $("#modal-root");
    let settled = false;

    const close = (value) => {
      if (settled) return;
      settled = true;
      mask.remove();
      resolve(value);
    };

    const footer = h("div", { class: "modal-foot" });
    (options.buttons || [{ label: "关闭", kind: "primary" }]).forEach((button) => {
      footer.appendChild(h("button", {
        class: button.kind === "primary" ? "primary" : "ghost",
        text: button.label,
        onclick: async () => {
          if (button.action) {
            // action 可以是同步或异步；返回 false 表示校验不通过、保持弹窗打开
            const result = await button.action();
            if (result === false) return;
            close(result === undefined ? button.value : result);
            return;
          }
          close(button.value);
        },
      }));
    });

    const modal = h("div", { class: "modal" }, [
      h("div", { class: "modal-head", text: options.title || "" }),
      h("div", { class: "modal-body" }, options.body),
      footer,
    ]);
    const mask = h("div", {
      class: "modal-mask",
      onclick: (event) => { if (event.target === mask) close(undefined); },
    }, modal);

    root.appendChild(mask);
    const focusable = modal.querySelector("input, textarea, select, button");
    if (focusable) focusable.focus();
    if (options.onClose) options.onClose(close);
  });
}

/** 只读文本弹窗（查看最终配置 / 导出模板等）。 */
async function showTextModal(title, text, downloadName) {
  const body = h("div", {}, [h("pre", { text })]);
  const buttons = [{ label: "关闭" }];
  if (downloadName) {
    buttons.unshift({
      label: "下载文件",
      action: () => {
        const blob = new Blob([text], { type: "text/yaml;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const link = h("a", { href: url, download: downloadName });
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
        return undefined;
      },
    });
  }
  buttons.unshift({
    label: "复制",
    action: () => {
      navigator.clipboard && navigator.clipboard.writeText(text);
      toast("已复制到剪贴板", "ok");
      return undefined;
    },
  });
  return showModal({ title, body, buttons });
}
