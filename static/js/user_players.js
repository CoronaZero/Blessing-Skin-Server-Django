/* 角色列表页：卡片网格 + 管理/删除。 */

(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  async function load() {
    const list = document.getElementById("players-list");
    if (!list) return;
    let payload;
    try {
      payload = await window.bsFetch("/user/players");
    } catch (err) {
      list.innerHTML =
        '<p style="grid-column:1/-1;text-align:center;color:var(--bs-danger);padding:24px 0">' +
        "角色加载失败：" + err + "<br>请刷新页面重试（Ctrl+F5 清缓存）</p>";
      return;
    }
    if (payload.code !== 0) {
      list.innerHTML =
        '<p style="grid-column:1/-1;text-align:center;color:var(--bs-danger);padding:24px 0">' +
        (payload.message || "角色加载失败") + "</p>";
      return;
    }
    list.innerHTML = "";
    payload.data.forEach(function (p) {
      const el = document.createElement("div");
      el.className = "bs-card";
      el.innerHTML =
        '<div style="padding:14px;text-align:center">' +
        '<img src="/preview/0?png" hidden>' +
        '<img src="/avatar/128/' + encodeURIComponent(p.name) + '.png"' +
        '     style="width:72px;height:72px;border-radius:10px">' +
        '<div style="font-weight:600;margin-top:8px">' + escapeHtml(p.name) + "</div>" +
        '<div style="font-size:12px;color:var(--bs-text-secondary);margin-top:2px">UUID ' +
        escapeHtml(p.uuid.slice(0, 12)) + "…</div>" +
        '<div style="display:flex;gap:8px;justify-content:center;margin-top:10px">' +
        '<a class="bs-btn bs-btn-primary" style="padding:5px 14px;font-size:13px"' +
        '   href="/user/players/' + p.pid + '">管理</a>' +
        '<button class="bs-btn bs-btn-secondary" style="padding:5px 14px;font-size:13px"' +
        '        data-del="' + p.pid + '" data-name="' + escapeHtml(p.name) + '">删除</button>' +
        "</div></div>";
      list.appendChild(el);
    });
    list.querySelectorAll("[data-del]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        if (!window.confirm("确定删除角色 " + btn.dataset.name + "？")) return;
        const r = await window.bsPost("/user/players/" + btn.dataset.del + "/delete");
        window.bsToast(r.message, r.code === 0 ? "success" : "error");
        if (r.code === 0) load();
      });
    });
  }

  document.getElementById("add-player-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await window.bsPost("/user/players", new FormData(this));
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
    if (r.code === 0) { this.reset(); load(); }
  });

  load();
})();
