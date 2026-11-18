/* 通知页：全量通知 + 已读标记 + 单条删除 + 清空全部。 */

(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  async function load() {
    const box = document.querySelector("#notifications-list .card-body");
    if (box) {
      const payload = await window.bsFetch("/user/notifications");
      if (payload.code !== 0) return;
      render(box, payload);
    }
  }

  /* 顶部操作按钮（全部已读 / 清空全部）共用 POST + 刷新逻辑 */
  function wireTopButton(id, confirmMsg) {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.addEventListener("click", async function () {
      if (confirmMsg && !window.confirm(confirmMsg)) return;
      btn.disabled = true;
      try {
        const r = await window.bsPost(btn.dataset.url, new FormData());
        window.bsToast(r.message, r.code === 0 ? "success" : "error");
        if (r.code === 0) load();
      } catch (err) {
        window.bsToast("操作失败：" + err, "error");
      } finally {
        btn.disabled = false;
      }
    });
  }
  wireTopButton("read-all-btn");
  wireTopButton("clear-all-btn", "确定清空全部通知？该操作不可恢复。");

  function render(box, payload) {
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
        '<div style="display:flex;gap:6px;align-self:center;flex-shrink:0">' +
        (n.read ? "" :
          '<button class="bs-btn bs-btn-secondary" style="padding:4px 12px;font-size:12px"' +
          '        data-read="' + n.id + '">已读</button>') +
        '<button class="bs-btn bs-btn-secondary" style="padding:4px 12px;font-size:12px;color:var(--bs-danger)"' +
        '        data-delete="' + n.id + '">删除</button>' +
        "</div></div>";
      const readBtn = el.querySelector("[data-read]");
      if (readBtn) {
        readBtn.addEventListener("click", function () {
          postRow(readBtn, "/user/notifications/" + n.id + "/read");
        });
      }
      const deleteBtn = el.querySelector("[data-delete]");
      if (deleteBtn) {
        deleteBtn.addEventListener("click", function () {
          if (!window.confirm("确定删除该通知？")) return;
          postRow(deleteBtn, "/user/notifications/" + n.id + "/delete");
        });
      }
      box.appendChild(el);
    });
  }

  async function postRow(btn, url) {
    btn.disabled = true;
    try {
      const r = await window.bsPost(url);
      if (r.code === 0) {
        load();
      } else {
        window.bsToast(r.message, "error");
        btn.disabled = false;
      }
    } catch (err) {
      window.bsToast("操作失败：" + err, "error");
      btn.disabled = false;
    }
  }

  load();
})();