/* 上传页：本地实时 3D 预览 + 提交。 */

(function () {
  "use strict";

  const form = document.getElementById("upload-form");
  const alertBox = document.getElementById("upload-alert");
  const submitBtn = document.getElementById("upload-submit");
  const fileInput = document.getElementById("f-file");
  const typeSelect = document.getElementById("f-type");
  const previewBox = document.getElementById("preview-box");

  let viewer = null;

  function showPreview(dataUrl, isCape) {
    previewBox.style.display = "block";
    if (isCape) {
      // 披风用平铺图片展示
      if (viewer) { viewer = null; }
      const canvas = document.getElementById("upload-viewer");
      canvas.outerHTML =
        '<img id="upload-viewer" src="' + dataUrl + '" alt="披风预览"' +
        ' style="max-height:280px;border-radius:8px;background:var(--bs-primary-light)">';
      return;
    }
    const canvas = document.getElementById("upload-viewer");
    if (typeof skinview3d === "undefined") return;
    canvas.outerHTML =
      '<canvas id="upload-viewer" width="200" height="280" style="max-width:100%"></canvas>';
    viewer = new skinview3d.SkinViewer({
      canvas: document.getElementById("upload-viewer"),
      width: 200,
      height: 280,
      skin: dataUrl,
    });
    viewer.autoRotate = true;
    viewer.zoom = 0.75;
    viewer.animation = new skinview3d.IdleAnimation();
  }

  fileInput.addEventListener("change", function () {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function (e) {
      const isCape = typeSelect.value === "cape";
      showPreview(e.target.result, isCape);
    };
    reader.readAsDataURL(file);
  });
  typeSelect.addEventListener("change", function () {
    if (fileInput.files && fileInput.files[0]) {
      fileInput.dispatchEvent(new Event("change"));
    }
  });

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    submitBtn.disabled = true;
    try {
      const payload = await window.bsPost("/skinlib/upload", new FormData(form));
      if (payload.code === 0) {
        window.bsToast("上传成功，正在跳转…", "success");
        setTimeout(function () { window.location.href = "/skinlib"; }, 900);
        return;
      }
      if (payload.code === 2 && payload.data && payload.data.tid) {
        alertBox.className = "bs-alert bs-alert-error";
        alertBox.textContent = "该纹理已存在于皮肤库（去重），无需重复上传";
        alertBox.style.display = "block";
        return;
      }
      alertBox.className = "bs-alert bs-alert-error";
      alertBox.textContent = payload.message || "上传失败";
      alertBox.style.display = "block";
    } catch (err) {
      alertBox.className = "bs-alert bs-alert-error";
      alertBox.textContent = "网络异常，请稍后再试";
      alertBox.style.display = "block";
    } finally {
      submitBtn.disabled = false;
    }
  });
})();
