/* 全局交互脚本：JSON API 调用约定、消息条、暗色模式 */

(function () {
  "use strict";

  // ---- JSON API 助手（对照后端 {code, message, data} 契约）----
  window.bsFetch = async function (url, options) {
    options = options || {};
    options.headers = Object.assign(
      { "X-Requested-With": "XMLHttpRequest" },
      options.headers || {}
    );
    const resp = await fetch(url, options);
    const payload = await resp.json().catch(() => null);
    if (payload === null) {
      throw new Error("响应不是有效 JSON");
    }
    return payload; // {code, message, data}
  };

  // CSRF（Django 范式：表单与头部双通道）
  window.bsCsrfToken = function () {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : "";
  };

  window.bsPost = async function (url, formData) {
    formData = formData instanceof FormData ? formData : new FormData(formData);
    return window.bsFetch(url, {
      method: "POST",
      body: formData,
      headers: { "X-CSRFToken": window.bsCsrfToken() },
    });
  };

  // ---- 消息条 ----
  window.bsToast = function (message, kind) {
    let container = document.querySelector(".bs-toast-container");
    if (!container) {
      container = document.createElement("div");
      container.className = "bs-toast-container";
      container.style.cssText =
        "position:fixed;top:68px;right:16px;z-index:100;display:flex;flex-direction:column;gap:8px;";
      document.body.appendChild(container);
    }
    const el = document.createElement("div");
    el.className = "bs-alert bs-alert-" + (kind || "success");
    el.style.cssText = "min-width:220px;box-shadow:var(--bs-shadow);margin:0;";
    el.textContent = message;
    container.appendChild(el);
    setTimeout(function () {
      el.remove();
    }, 3600);
  };

  // ---- 暗色模式（后端 is_dark_mode → data-theme）----
  function applyTheme(dark) {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  }
  window.bsApplyTheme = applyTheme;

  // ---- 图标内联（静态引用 → use）----
  window.bsIcon = function (name) {
    return (
      '<svg class="bs-icon" aria-hidden="true"><use href="/static/icons/' +
      name +
      '.svg#' +
      name +
      '"></use></svg>'
    );
  };
})();
