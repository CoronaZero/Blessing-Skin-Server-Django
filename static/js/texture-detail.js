/* 纹理详情页：3D 预览 + 衣柜/举报操作 */

(function () {
  "use strict";

  // 3D 预览
  const canvas = document.getElementById("viewer-canvas");
  if (canvas) {
    window.bsInitSkinViewer(canvas, {
      skin: canvas.dataset.skin,
      model: canvas.dataset.model,
      width: 280,
      height: 360,
    });
  }

  // 加入衣柜
  const closetBtn = document.getElementById("closet-btn");
  if (closetBtn) {
    closetBtn.addEventListener("click", async function () {
      const fd = new FormData();
      fd.set("tid", closetBtn.dataset.tid);
      const r = await window.bsPost("/user/closet", fd);
      window.bsToast(r.message, r.code === 0 ? "success" : "error");
      if (r.code === 0) closetBtn.disabled = true;
    });
  }

  // 举报
  const reportBtn = document.getElementById("report-btn");
  if (reportBtn) {
    reportBtn.addEventListener("click", async function () {
      const reason = window.prompt("请填写举报理由：");
      if (!reason) return;
      const fd = new FormData();
      fd.set("tid", reportBtn.dataset.tid);
      fd.set("reason", reason);
      const r = await window.bsPost("/user/report", fd);
      window.bsToast(r.message, r.code === 0 ? "success" : "error");
    });
  }

  // 材质管理（上传者/管理员）
  const privacyBtn = document.getElementById("privacy-btn");
  if (privacyBtn) {
    privacyBtn.addEventListener("click", async function () {
      privacyBtn.disabled = true;
      try {
        const fd = new FormData();
        fd.set("tid", privacyBtn.dataset.tid);
        const r = await window.bsPost(
          "/skinlib/texture/" + privacyBtn.dataset.tid + "/privacy", fd
        );
        window.bsToast(r.message, r.code === 0 ? "success" : "error");
        if (r.code === 0) {
          setTimeout(function () { location.reload(); }, 600);
        }
      } finally {
        privacyBtn.disabled = false;
      }
    });
  }

  const renameBtn = document.getElementById("rename-btn");
  if (renameBtn) {
    renameBtn.addEventListener("click", async function () {
      const name = window.prompt("新的材质名称：", renameBtn.dataset.name);
      if (!name || !name.trim()) return;
      const fd = new FormData();
      fd.set("name", name.trim());
      const r = await window.bsPost(
        "/skinlib/texture/" + renameBtn.dataset.tid + "/name", fd
      );
      window.bsToast(r.message, r.code === 0 ? "success" : "error");
      if (r.code === 0) {
        setTimeout(function () { location.reload(); }, 600);
      }
    });
  }

  const deleteBtn = document.getElementById("delete-btn");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", async function () {
      if (!window.confirm("确定删除该材质？佩戴它的角色将被置空，操作不可恢复。")) {
        return;
      }
      const fd = new FormData();
      fd.set("tid", deleteBtn.dataset.tid);
      const r = await window.bsPost(
        "/skinlib/texture/" + deleteBtn.dataset.tid + "/remove", fd
      );
      if (r.code === 0) {
        window.bsToast("已删除，正在返回皮肤库…", "success");
        setTimeout(function () { location.href = "/skinlib"; }, 600);
      } else {
        window.bsToast(r.message, "error");
      }
    });
  }
})();
