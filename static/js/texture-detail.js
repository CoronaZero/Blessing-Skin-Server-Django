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
})();
