/* 全局交互脚本：JSON API 调用约定、消息条、夜间/日间模式 */

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

  // CSRF（双通道：表单隐藏域优先，cookie 兜底——签到等非表单 POST 依赖后者）
  window.bsCsrfToken = function () {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input && input.value) return input.value;
    // Django CSRF_COOKIE（默认 HttpOnly=false，JS 可读）
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
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
        "position:fixed;top:68px;right:16px;z-index:1090;display:flex;flex-direction:column;gap:8px;";
      document.body.appendChild(container);
    }
    const el = document.createElement("div");
    el.className = "bs-alert bs-alert-" + (kind || "success");
    el.style.cssText = "min-width:220px;box-shadow:var(--bs-box-shadow);margin:0;";
    el.textContent = message;
    container.appendChild(el);
    setTimeout(function () {
      el.remove();
    }, 3600);
  };

  // ---- 夜间/日间模式（BS5 data-bs-theme；localStorage 持久化，
  //      未设置时跟随系统 prefers-color-scheme）----
  function applyTheme(dark) {
    document.documentElement.setAttribute(
      "data-bs-theme", dark ? "dark" : "light"
    );
    const icon = document.getElementById("theme-icon");
    if (icon) {
      icon.className = dark ? "bi bi-sun" : "bi bi-moon-stars";
    }
  }
  window.bsApplyTheme = applyTheme;

  (function initTheme() {
    const saved = localStorage.getItem("bs-theme");
    const dark =
      saved !== null
        ? saved === "dark"
        : window.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(dark);
    document.getElementById("theme-toggle")?.addEventListener("click", function () {
      const next =
        document.documentElement.getAttribute("data-bs-theme") !== "dark";
      applyTheme(next);
      localStorage.setItem("bs-theme", next ? "dark" : "light");
    });
  })();

  // ---- 顶栏下拉菜单的退出登录（BS5 dropdown 自管开合）----
  document.addEventListener("DOMContentLoaded", function () {
    const logoutBtn = document.getElementById("nav-logout");
    if (logoutBtn) {
      logoutBtn.addEventListener("click", async function () {
        const fd = new FormData();
        await window.bsPost("/auth/logout", fd);
        window.location.href = "/";
      });
    }
  });

  // ---- 用户抽屉：入口点击隐藏抽屉但不禁导航 ----
  // 注意不能用 <a data-bs-dismiss="offcanvas">：BS5 的 dismiss 委托对
  // <a> 标签一律 preventDefault()，真实 href 的跳转会被吞掉。
  document.addEventListener("click", function (e) {
    const link = e.target.closest("#user-drawer a[href], #pm-sidebar a[href]");
    if (!link) return;
    const drawer = link.closest(".offcanvas");
    if (drawer && window.bootstrap && bootstrap.Offcanvas) {
      bootstrap.Offcanvas.getInstance(drawer)?.hide();
    }
  });
  // 浏览器返回（bfcache 恢复）时抽屉若是开着的，关掉它
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    document.querySelectorAll(".offcanvas.show").forEach(function (el) {
      if (window.bootstrap && bootstrap.Offcanvas) {
        bootstrap.Offcanvas.getInstance(el)?.hide();
      }
    });
  });

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
