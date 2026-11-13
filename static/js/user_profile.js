/* 资料设置页：昵称/密码/注销。 */

(function () {
  "use strict";

  document.getElementById("nick-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const fd = new FormData(this);
    fd.set("action", "nickname");
    const r = await window.bsPost("/user/profile", fd);
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
  });

  document.getElementById("pass-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const fd = new FormData(this);
    fd.set("action", "password");
    const r = await window.bsPost("/user/profile", fd);
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
    if (r.code === 0) this.reset();
  });

  document.getElementById("delete-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    if (!window.confirm("确定永久注销账号？所有数据不可恢复！")) return;
    const fd = new FormData(this);
    fd.set("action", "delete");
    const r = await window.bsPost("/user/profile", fd);
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
    if (r.code === 0) setTimeout(function () { location.href = "/"; }, 1200);
  });

  /* Microsoft 账号解绑（按钮可能不存在，安全判空） */
  const msaUnbindBtn = document.getElementById("msa-unbind-btn");
  if (msaUnbindBtn) {
    msaUnbindBtn.addEventListener("click", async function () {
      if (!window.confirm("确定解绑 Microsoft 账号？解绑后将进入冷却期，期间不可重新绑定。")) return;
      const r = await window.bsPost("/oauth/microsoft/msa/unbind");
      window.bsToast(r.message, r.code === 0 ? "success" : "error");
      if (r.code === 0) setTimeout(function () { location.reload(); }, 900);
    });
  }
})();
