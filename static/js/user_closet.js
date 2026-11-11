/* 衣柜页：全量衣柜卡片 + 移除。 */

(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  async function load() {
    const list = document.getElementById("closet-list");
    const payload = await window.bsFetch("/user/closet");
    if (payload.code !== 0) return;
    list.innerHTML = "";
    if (!payload.data.length) {
      list.innerHTML =
        '<p style="grid-column:1/-1;text-align:center;color:var(--bs-text-secondary);padding:40px 0">' +
        '衣柜还是空的，去 <a href="/skinlib">皮肤库</a> 收藏一些吧</p>';
      return;
    }
    payload.data.forEach(function (item) {
      const el = document.createElement("div");
      el.className = "bs-card";
      el.innerHTML =
        '<div style="padding:10px;cursor:pointer" data-tid="' + item.tid + '">' +
        '<img src="/preview/' + item.tid + '?height=150" style="width:100%;border-radius:6px;background:var(--bs-primary-light)">' +
        '<div style="font-weight:600;margin-top:8px;font-size:14px">' + escapeHtml(item.name) + "</div>" +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">' +
        '<span style="font-size:12px;color:var(--bs-text-secondary)">' +
        (item.type === "cape" ? "披风" : "皮肤") + "</span>" +
        '<button class="bs-btn bs-btn-secondary" style="padding:2px 10px;font-size:12px"' +
        '        data-rm="' + item.id + '">移除</button></div></div>';
      el.querySelector("[data-tid]").addEventListener("click", function () {
        window.location.href = "/skinlib/show?tid=" + item.tid;
      });
      el.querySelector("[data-rm]").addEventListener("click", async function (e) {
        e.stopPropagation();
        const r = await window.bsPost("/user/closet/" + item.id + "/remove");
        window.bsToast(r.message, r.code === 0 ? "success" : "error");
        if (r.code === 0) load();
      });
      list.appendChild(el);
    });
  }

  load();
})();
