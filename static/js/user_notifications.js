/* 通知页：分页列表 + 已读标记 + 单条删除 + 清空全部。 */

(function () {
  "use strict";

  var currentPage = 1;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  async function load(page) {
    const box = document.querySelector("#notifications-list .card-body");
    if (box) {
      if (page) currentPage = page;
      const payload = await window.bsFetch(
        "/user/notifications?page=" + currentPage
      );
      if (payload.code !== 0) return;
      const info = payload.data || {};
      // 当前页被删空（如删除末页最后一条）后自动回落上一页
      if (!info.items || (!info.items.length && currentPage > 1)) {
        load(currentPage - 1);
        return;
      }
      render(box, info);
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
        if (r.code === 0) load(1); // 批量操作后回第一页
      } catch (err) {
        window.bsToast("操作失败：" + err, "error");
      } finally {
        btn.disabled = false;
      }
    });
  }
  wireTopButton("read-all-btn");
  wireTopButton("clear-all-btn", "确定清空全部通知？该操作不可恢复。");

  function renderPager(box, info) {
    if (info.num_pages <= 1) return;
    const pager = document.createElement("div");
    pager.style.cssText =
      "display:flex;gap:10px;justify-content:center;margin-top:16px;font-size:13px";
    if (info.has_prev) {
      const prev = document.createElement("button");
      prev.className = "btn btn-outline-secondary";
      prev.style.fontSize = "13px";
      prev.textContent = "上一页";
      prev.addEventListener("click", function () { load(info.page - 1); });
      pager.appendChild(prev);
    }
    const label = document.createElement("span");
    label.style.cssText =
      "align-self:center;color:var(--bs-text-secondary)";
    label.textContent = info.page + " / " + info.num_pages;
    pager.appendChild(label);
    if (info.has_next) {
      const next = document.createElement("button");
      next.className = "btn btn-outline-secondary";
      next.style.fontSize = "13px";
      next.textContent = "下一页";
      next.addEventListener("click", function () { load(info.page + 1); });
      pager.appendChild(next);
    }
    box.appendChild(pager);
  }

  function render(box, info) {
    box.innerHTML = "";
    if (!info.items.length) {
      box.innerHTML =
        '<p style="color:var(--bs-text-secondary);text-align:center;padding:32px 0">暂无通知</p>';
      return;
    }
    info.items.forEach(function (n) {
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
    renderPager(box, info);
  }

  async function postRow(btn, url) {
    btn.disabled = true;
    try {
      const r = await window.bsPost(url);
      if (r.code === 0) {
        load(); // 保持当前页刷新（删空时 load 内自动回落上一页）
      } else {
        window.bsToast(r.message, "error");
        btn.disabled = false;
      }
    } catch (err) {
      window.bsToast("操作失败：" + err, "error");
      btn.disabled = false;
    }
  }

  load(1);
})();