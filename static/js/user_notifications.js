/* 通知页：全量通知 + 已读标记。 */

(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  async function load() {
    const box = document.querySelector("#notifications-list .bs-card-body");
    const payload = await window.bsFetch("/user/notifications");
    if (payload.code !== 0) return;
    box.innerHTML = "";
    if (!payload.data.length) {
      box.innerHTML =
        '<p style="color:var(--bs-text-secondary);text-align:center;padding:32px 0">暂无通知</p>';
      return;
    }
    payload.data.forEach(function (n) {
      const el = document.createElement("div");
      el.style.cssText =
        "border-bottom:1px solid var(--bs-border);padding:14px 0;" + (n.read ? "opacity:0.6;" : "");
      el.innerHTML =
        '<div style="display:flex;justify-content:space-between;gap:10px">' +
        "<div><b>" + escapeHtml(n.title) + "</b>" +
        '<p style="margin:4px 0 0;font-size:13px;color:var(--bs-text-secondary)">' +
        escapeHtml(n.content) + "</p>" +
        '<p style="margin:4px 0 0;font-size:11px;color:var(--bs-text-secondary)">' +
        escapeHtml(n.created_at) + "</p></div>" +
        (n.read ? "" :
          '<button class="bs-btn bs-btn-secondary" style="padding:4px 12px;font-size:12px;align-self:center"' +
          '        data-read="' + n.id + '">已读</button>') +
        "</div>";
      const btn = el.querySelector("[data-read]");
      if (btn) {
        btn.addEventListener("click", async function () {
          const r = await window.bsPost("/user/notifications/" + n.id + "/read");
          if (r.code === 0) load();
        });
      }
      box.appendChild(el);
    });
  }

  load();
})();
