/* Cloudflare Turnstile 本地化装配。
 *
 * 注意：widget 脚本本体（challenges.cloudflare.com）由 CF 动态下发，
 * 站点自身资源全部本地提供；仅第三方验证组件按其官方要求加载。
 * 未启用时此文件不会被引用。
 */

(function () {
  "use strict";

  const slot = document.getElementById("captcha-slot");
  if (!slot) return;

  const sitekey = slot.dataset.sitekey;
  const tokenInput = document.getElementById("captcha-token");

  window.bsCaptchaToken = function () {
    return tokenInput ? tokenInput.value : "";
  };

  // 显式渲染（defer 语义，等待 CF API 就绪）
  function render() {
    if (typeof window.turnstile === "undefined") {
      setTimeout(render, 200);
      return;
    }
    window.turnstile.render(slot, {
      sitekey: sitekey,
      callback: function (token) {
        if (tokenInput) tokenInput.value = token;
      },
      "expired-callback": function () {
        if (tokenInput) tokenInput.value = "";
      },
    });
  }

  if (!document.getElementById("cf-turnstile-script")) {
    const s = document.createElement("script");
    s.id = "cf-turnstile-script";
    s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    s.async = true;
    s.onload = render;
    document.head.appendChild(s);
  } else {
    render();
  }
})();
