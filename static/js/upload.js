/* 上传页交互：消费 /skinlib/upload JSON API。 */

(function () {
  "use strict";

  const form = document.getElementById("upload-form");
  const alertBox = document.getElementById("upload-alert");
  const submitBtn = document.getElementById("upload-submit");

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
