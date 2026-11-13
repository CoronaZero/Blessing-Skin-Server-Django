/* 上传页：本地实时 3D 预览 + 提交。 */

(function () {
  "use strict";

  const form = document.getElementById("upload-form");
  const alertBox = document.getElementById("upload-alert");
  const submitBtn = document.getElementById("upload-submit");
  const fileInput = document.getElementById("f-file");
  const typeSelect = document.getElementById("f-type");
  const previewBox = document.getElementById("preview-box");
  const capeHint = document.getElementById("cape-hint");

  let viewer = null;

  function showPreview(dataUrl, isCape) {
    previewBox.style.display = "block";
    // 重建前清理：旧 viewer 的渲染循环（防泄漏）与旧控制条（防重复注入）
    if (viewer) {
      try { viewer.dispose(); } catch (e) { /* 旧版本无 dispose */ }
      viewer = null;
    }
    previewBox
      .querySelectorAll(".bs-viewer-controls")
      .forEach(function (el) { el.remove(); });
    const canvas = document.getElementById("upload-viewer");

    // 披风：挂到管理员配置的默认皮肤上做 3D 预览；未配置则回落平铺图
    if (isCape) {
      if (window.BS_DEFAULT_SKIN && typeof skinview3d !== "undefined") {
        if (capeHint) capeHint.style.display = "block";
        canvas.outerHTML =
          '<canvas id="upload-viewer" width="200" height="280" style="max-width:100%;border-radius:8px;background:var(--bs-primary-light)"></canvas>';
        viewer = window.bsInitSkinViewer(document.getElementById("upload-viewer"), {
          skin: window.BS_DEFAULT_SKIN.url,
          cape: dataUrl,
          model: window.BS_DEFAULT_SKIN.model === "slim" ? "slim" : "default",
          width: 200,
          height: 280,
        });
        return;
      }
      // 未配置默认皮肤：平铺图
      if (capeHint) capeHint.style.display = "none";
      viewer = null;
      canvas.outerHTML =
        '<img id="upload-viewer" src="' + dataUrl + '" alt="披风预览"' +
        ' style="max-height:280px;border-radius:8px;background:var(--bs-primary-light)">';
      return;
    }

    if (capeHint) capeHint.style.display = "none";
    if (typeof skinview3d === "undefined") return;
    canvas.outerHTML =
      '<canvas id="upload-viewer" width="200" height="280" style="max-width:100%"></canvas>';
    // alex 模型须显式指定 slim，否则一律按宽臂渲染（与详情页预览一致）
    viewer = window.bsInitSkinViewer(document.getElementById("upload-viewer"), {
      skin: dataUrl,
      model: typeSelect.value === "alex" ? "slim" : "default",
      width: 200,
      height: 280,
    });
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
