/* skinview3d 装配：本地 vendor，无 CDN。
 *
 * 用法（页面回调）：
 *   window.bsInitSkinViewer(canvas, { skin, cape, slim, width, height })
 */

(function () {
  "use strict";

  window.bsInitSkinViewer = function (canvas, options) {
    if (typeof skinview3d === "undefined") {
      console.error("skinview3d 未加载（检查 /static/vendor/skinview3d.bundle.js）");
      return null;
    }
    var viewer = new skinview3d.SkinViewer({
      canvas: canvas,
      width: options.width || 300,
      height: options.height || 400,
      skin: options.skin || null,
    });
    if (options.cape) {
      viewer.loadCape(options.cape);
    }
    if (options.model === "slim") {
      viewer.loadSkin(options.skin, { model: "slim" });
    }
    viewer.autoRotate = true;
    viewer.fov = 60;
    viewer.zoom = 0.85;
    // 待机呼吸动画
    viewer.animation = new skinview3d.IdleAnimation();
    return viewer;
  };
})();
