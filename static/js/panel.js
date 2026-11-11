/* 管理面板通用交互：行操作统一走 bsPost + 刷新。 */

(function () {
  "use strict";

  document.addEventListener("click", async function (e) {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    e.preventDefault();

    const action = btn.dataset.action;
    const url = btn.dataset.url;
    const confirmMsg = btn.dataset.confirm;

    const fd = new FormData();
    fd.set("action", action);
    // 附加 data-arg-* 参数
    for (const attr of btn.attributes) {
      if (attr.name.startsWith("data-arg-")) {
        fd.set(attr.name.slice(9), attr.value);
      }
    }

    if (confirmMsg && !window.confirm(confirmMsg)) return;
    const r = await window.bsPost(url, fd);
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
    if (r.code === 0) setTimeout(function () { location.reload(); }, 700);
  });
})();
