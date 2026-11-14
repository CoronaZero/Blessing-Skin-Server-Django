/* 资料设置页：昵称/密码/注销/Microsoft 解绑。
 *
 * 全部绑定安全判空：delete-form 仅非管理员渲染（模板 is_admin 豁免）、
 * msa-unbind-btn 仅绑定了 Microsoft 账号的用户渲染——顺序绑定中任一
 * 元素缺失若直接 addEventListener 会抛 TypeError，中断后续所有绑定。
 */

(function () {
  "use strict";

  function wire(id, handler) {
    const el = document.getElementById(id);
    if (el) el.addEventListener("submit", handler);
  }

  wire("nick-form", async function (e) {
    e.preventDefault();
    const fd = new FormData(this);
    fd.set("action", "nickname");
    const r = await window.bsPost("/user/profile", fd);
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
  });

  wire("pass-form", async function (e) {
    e.preventDefault();
    const fd = new FormData(this);
    fd.set("action", "password");
    const r = await window.bsPost("/user/profile", fd);
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
    if (r.code === 0) this.reset();
  });

  wire("delete-form", async function (e) {
    e.preventDefault();
    if (!window.confirm("确定永久注销账号？所有数据不可恢复！")) return;
    const fd = new FormData(this);
    fd.set("action", "delete");
    const r = await window.bsPost("/user/profile", fd);
    window.bsToast(r.message, r.code === 0 ? "success" : "error");
    if (r.code === 0) setTimeout(function () { location.href = "/"; }, 1200);
  });

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
