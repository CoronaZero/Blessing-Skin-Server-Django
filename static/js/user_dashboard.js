/* 仪表盘交互（概览+签到+快速配置启动器）。 */

(function () {
  "use strict";

  document.getElementById("sign-btn").addEventListener("click", async function () {
    const btn = this;
    btn.disabled = true;
    const r = await window.bsPost("/user/sign");
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
    if (r.code === 0 && r.data) {
      const scoreEl = document.getElementById("uc-score");
      if (scoreEl) scoreEl.textContent = "积分：" + r.data.score;
      // 与后端 _sign_state 同一文案（「N 小时/分钟后可再签」）
      if (r.data.next_sign_text) {
        btn.textContent = "已签到 · " + r.data.next_sign_text + "后可再签";
      } else {
        btn.textContent = "已签到";
      }
    } else {
      setTimeout(function () { btn.disabled = false; }, 2000);
    }
  });

  /* 快速配置启动器（对照原版 yggdrasil-api 插件 dnd.js）：
   * 拖拽传输 text/plain，内容为启动器识别协议
   * "authlib-injector:yggdrasil-server:<API URL>"（URL 经 encodeURIComponent）；
   * 点击复制 API 地址（clipboard API 不可用时回落 execCommand）。 */
  const dndBtn = document.getElementById("ygg-dnd-button");
  if (dndBtn) {
    const apiUrl = dndBtn.dataset.apiUrl || "";
    const originalText = dndBtn.innerHTML;

    dndBtn.addEventListener("dragstart", function (e) {
      const uri = "authlib-injector:yggdrasil-server:" + encodeURIComponent(apiUrl);
      if (e.dataTransfer) {
        e.dataTransfer.setData("text/plain", uri);
        e.dataTransfer.dropEffect = "copy";
      }
    });

    dndBtn.addEventListener("click", async function () {
      let copied = false;
      try {
        await navigator.clipboard.writeText(apiUrl);
        copied = true;
      } catch (err) {
        const tmp = document.createElement("input");
        document.body.appendChild(tmp);
        tmp.value = apiUrl;
        tmp.select();
        copied = document.execCommand("copy");
        tmp.remove();
      }
      if (copied) {
        dndBtn.innerHTML = '<i class="bi bi-check2 me-1"></i>已复制';
        dndBtn.disabled = true;
        setTimeout(function () {
          dndBtn.innerHTML = originalText;
          dndBtn.disabled = false;
        }, 1000);
      } else {
        window.bsToast("复制失败，请手动复制：" + apiUrl, "error");
      }
    });
  }
})();