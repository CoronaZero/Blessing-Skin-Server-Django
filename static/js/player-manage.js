/* 角色管理页交互：改名 / 应用皮肤披风（衣柜选择）/ 清除 / 删除 */

(function () {
  "use strict";

  const pid = new URLSearchParams(window.location.search).get("pid");
  const pidFromPath = window.location.pathname.split("/").filter(Boolean)[2];

  function post(url, fd) {
    return window.bsPost(url, fd || new FormData());
  }

  // 改名
  const renameForm = document.getElementById("pm-rename-form");
  renameForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    const r = await post("/user/players/" + pidFromPath + "/name", new FormData(this));
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
  });

  // 应用皮肤 / 披风
  function bindApply(listId, kind) {
    const list = document.getElementById(listId);
    list.addEventListener("click", async function (e) {
      const card = e.target.closest("[data-tid]");
      if (!card) return;
      const fd = new FormData();
      fd.set("tid", card.dataset.tid);
      const r = await post("/user/players/" + pidFromPath + "/" + kind, fd);
      window.bsToast(r.message, r.code === 0 ? "success" : "error");
      if (r.code === 0) setTimeout(function () { location.reload(); }, 600);
    });
  }
  bindApply("pm-skin-list", "skin");
  bindApply("pm-cape-list", "cape");

  // 清除皮肤 / 披风
  function bindClear(btnId) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.addEventListener("click", async function () {
      const fd = new FormData();
      fd.set("tid", "0");
      const r = await post(btn.dataset.url, fd);
      window.bsToast(r.message, r.code === 0 ? "success" : "error");
      if (r.code === 0) setTimeout(function () { location.reload(); }, 600);
    });
  }
  bindClear("pm-clear-skin");
  bindClear("pm-clear-cape");

  // 删除角色
  document.getElementById("pm-delete-btn").addEventListener("click", async function () {
    if (!window.confirm("确定删除角色？该操作不可恢复。")) return;
    const r = await post("/user/players/" + pidFromPath + "/delete");
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
    if (r.code === 0) setTimeout(function () { location.href = "/user#players"; }, 700);
  });

  _ = pid;
})();
