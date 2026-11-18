/* 仪表盘交互（概览+签到）。 */

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
})();