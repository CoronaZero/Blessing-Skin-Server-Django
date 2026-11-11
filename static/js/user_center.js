/* 用户中心交互：角色/衣柜/通知/签到/资料（消费已实现的 JSON API） */

(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---- 渲染用户概要 ----
  async function loadSummary() {
    if (!window.BS_USER) return;
    document.getElementById("uc-nickname").textContent = window.BS_USER.nickname;
    document.getElementById("uc-email").textContent = window.BS_USER.email;
    document.getElementById("uc-score").textContent =
      "积分：" + window.BS_USER.score;
    const player = window.BS_USER.players && window.BS_USER.players[0];
    document.getElementById("uc-avatar").src = player
      ? "/avatar/128/" + encodeURIComponent(player.name) + ".png"
      : "/static/icons/user.svg";
  }

  // ---- 角色列表 ----
  async function loadPlayers() {
    const list = document.getElementById("players-list");
    const payload = await window.bsFetch("/user/players");
    if (payload.code !== 0) return;
    list.innerHTML = "";
    payload.data.forEach(function (p) {
      const el = document.createElement("div");
      el.style.cssText =
        "border:1px solid var(--bs-border);border-radius:8px;padding:10px;text-align:center;min-width:110px";
      el.innerHTML =
        '<img src="/avatar/64/' + encodeURIComponent(p.name) + '.png"' +
        '     style="width:48px;height:48px;image-rendering:pixelated"><div' +
        '     style="font-weight:600;margin-top:6px">' + escapeHtml(p.name) + "</div>" +
        '<button class="bs-btn bs-btn-secondary" style="padding:3px 10px;font-size:12px' +
        ';margin-top:6px" data-del="' + p.pid + '">删除</button>';
      el.querySelector("[data-del]").addEventListener("click", async function () {
        if (!window.confirm("确定删除角色 " + p.name + "？")) return;
        const r = await window.bsPost("/user/players/" + p.pid + "/delete");
        window.bsToast(r.message, r.code === 0 ? "success" : "error");
        if (r.code === 0) loadPlayers();
      });
      list.appendChild(el);
    });
  }

  // ---- 衣柜 ----
  async function loadCloset() {
    const list = document.getElementById("closet-list");
    const payload = await window.bsFetch("/user/closet");
    if (payload.code !== 0) return;
    list.innerHTML = payload.data.length
      ? ""
      : '<p style="color:var(--bs-text-secondary)">衣柜还是空的，去皮肤库看看吧</p>';
    payload.data.forEach(function (item) {
      const el = document.createElement("div");
      el.className = "bs-card";
      el.innerHTML =
        '<div style="padding:10px">' +
        '<img src="/textures/' + item.hash + '" style="width:100%;image-rendering:pixelated' +
        ';border-radius:6px;background:var(--bs-primary-light)">' +
        '<div style="font-weight:600;margin-top:6px;font-size:14px">' +
        escapeHtml(item.name) + "</div></div>";
      list.appendChild(el);
    });
  }

  // ---- 通知 ----
  async function loadNotifications() {
    const list = document.getElementById("notifications-list");
    const payload = await window.bsFetch("/user/notifications");
    if (payload.code !== 0) return;
    list.innerHTML = payload.data.length
      ? ""
      : '<p style="color:var(--bs-text-secondary)">暂无通知</p>';
    payload.data.forEach(function (n) {
      const el = document.createElement("div");
      el.style.cssText =
        "border-bottom:1px solid var(--bs-border);padding:10px 0;" +
        (n.read ? "opacity:0.6;" : "");
      el.innerHTML =
        '<div style="display:flex;justify-content:space-between;gap:8px">' +
        "<b>" + escapeHtml(n.title) + "</b>" +
        (n.read ? "" : '<button class="bs-btn bs-btn-secondary" style="padding:2px 8px;' +
          'font-size:12px" data-read="' + n.id + '">已读</button>') +
        "</div><div>" + escapeHtml(n.content) + "</div>";
      const btn = el.querySelector("[data-read]");
      if (btn) {
        btn.addEventListener("click", async function () {
          const r = await window.bsPost("/user/notifications/" + n.id + "/read");
          if (r.code === 0) loadNotifications();
        });
      }
      list.appendChild(el);
    });
  }

  // ---- 签到 ----
  document.getElementById("sign-btn").addEventListener("click", async function () {
    const btn = this;
    btn.disabled = true;
    const r = await window.bsPost("/user/sign");
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
    if (r.code === 0 && r.data) {
      document.getElementById("uc-score").textContent = "积分：" + r.data.score;
    }
    setTimeout(function () { btn.disabled = false; }, 2000);
  });

  // ---- 表单 ----
  document.getElementById("add-player-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await window.bsPost("/user/players", new FormData(this));
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
    if (r.code === 0) { this.reset(); loadPlayers(); }
  });

  document.getElementById("nick-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const fd = new FormData(this);
    fd.set("action", "nickname");
    const r = await window.bsPost("/user/profile", fd);
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
    if (r.code === 0) document.getElementById("uc-nickname").textContent =
      fd.get("new_nickname");
  });

  document.getElementById("pass-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const fd = new FormData(this);
    fd.set("action", "password");
    const r = await window.bsPost("/user/profile", fd);
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
    if (r.code === 0) this.reset();
  });

  // ---- 导航高亮 ----
  const navLinks = document.querySelectorAll(".uc-nav");
  navLinks.forEach(function (a) {
    a.addEventListener("click", function () {
      navLinks.forEach(function (x) { x.style.background = ""; });
      this.style.background = "var(--bs-primary-light)";
    });
  });

  loadSummary();
  loadPlayers();
  loadCloset();
  loadNotifications();
})();
