/* skinview3d 装配与预览控制条（对照 PHP 原版 Viewer.tsx）：
 *
 * - 四态动画循环：行走 → 奔跑 → 飞行 → 待机（点击切换，对齐原版默认行走）
 * - 暂停/播放（暂停时同步关闭自动旋转，原版同款）
 * - 自动旋转开关
 * - 披风 ⇄ 鞘翅切换（仅加载了披风的查看器显示该按钮）
 * - 纯色背景切换：白 / 黑 / 灰（暗色主题默认灰，原版同款）
 *
 * 控制条由本文件注入 canvas 父容器，页面无需任何标记。
 * 用法（页面回调）：
 *   window.bsInitSkinViewer(canvas, { skin, cape, model, width, height })
 */

(function () {
  "use strict";

  var BG_GRAY = "#6c757d";

  function iconBtn(action, icon, title) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-outline-secondary btn-sm";
    btn.setAttribute("data-ctrl", action);
    btn.title = title;
    btn.innerHTML = '<i class="bi ' + icon + '"></i>';
    return btn;
  }

  function colorDot(color, title) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm rounded-circle p-0 border";
    btn.setAttribute("data-ctrl", "bg");
    btn.dataset.color = color;
    btn.title = title;
    btn.style.cssText = "width:22px;height:22px;background:" + color;
    return btn;
  }

  function buildControls(canvas, viewer, hasCape) {
    var wrap = document.createElement("div");
    wrap.className = "d-flex flex-wrap justify-content-center align-items-center gap-1";
    wrap.style.marginBottom = "8px";

    // 动作四态（顺序对齐原版 animationFactories：行走/奔跑/飞行/待机）
    var anims = [
      {
        name: "行走",
        icon: "bi-person-walking",
        make: function () { return new skinview3d.WalkingAnimation(); },
      },
      {
        name: "奔跑",
        icon: "bi-person-running",
        make: function () { return new skinview3d.RunningAnimation(); },
      },
      {
        name: "飞行",
        icon: "bi-airplane",
        make: function () { return new skinview3d.FlyingAnimation(); },
      },
      {
        name: "待机",
        icon: "bi-person-standing",
        make: function () { return new skinview3d.IdleAnimation(); },
      },
    ];
    var animIndex = 0;
    var paused = false;

    var animBtn = iconBtn("anim", anims[0].icon, "动作：" + anims[0].name);
    var pauseBtn = iconBtn("pause", "bi-pause-fill", "暂停动画");
    var rotateBtn = iconBtn("rotate", "bi-arrow-repeat", "自动旋转：开");

    wrap.appendChild(animBtn);
    wrap.appendChild(pauseBtn);
    wrap.appendChild(rotateBtn);

    if (hasCape) {
      var capeBtn = iconBtn("cape", "bi-feather", "披风 ⇄ 鞘翅");
      var elytra = false;
      capeBtn.addEventListener("click", function () {
        elytra = !elytra;
        viewer.playerObject.backEquipment = elytra ? "elytra" : "cape";
        capeBtn.style.opacity = elytra ? "1" : "";
      });
      wrap.appendChild(capeBtn);
    }

    // 背景纯色（原版 footer 的 White/Black/Gray 三色圆点）
    var colors = document.createElement("div");
    colors.className = "d-flex align-items-center gap-1 ms-1";
    [
      ["#ffffff", "白色背景"],
      ["#000000", "黑色背景"],
      [BG_GRAY, "灰色背景"],
    ].forEach(function (item) {
      colors.appendChild(colorDot(item[0], item[1]));
    });
    wrap.appendChild(colors);

    function applyPaused(next) {
      paused = next;
      if (viewer.animation) {
        viewer.animation.paused = paused;
      }
      if (paused) {
        viewer.autoRotate = false; // 原版 togglePause：暂停时停旋转
      }
      pauseBtn.innerHTML = '<i class="bi ' + (paused ? "bi-play-fill" : "bi-pause-fill") + '"></i>';
      pauseBtn.title = paused ? "播放动画" : "暂停动画";
      rotateBtn.style.opacity = viewer.autoRotate ? "" : "0.5";
    }

    animBtn.addEventListener("click", function () {
      animIndex = (animIndex + 1) % anims.length;
      var anim = anims[animIndex];
      viewer.animation = anim.make();
      animBtn.innerHTML = '<i class="bi ' + anim.icon + '"></i>';
      animBtn.title = "动作：" + anim.name + "（点击切换）";
      applyPaused(false); // 原版 toggleAnimation：切动作即恢复播放
    });

    pauseBtn.addEventListener("click", function () {
      applyPaused(!paused);
    });

    rotateBtn.addEventListener("click", function () {
      viewer.autoRotate = !viewer.autoRotate;
      rotateBtn.style.opacity = viewer.autoRotate ? "" : "0.5";
      rotateBtn.title = "自动旋转：" + (viewer.autoRotate ? "开" : "关");
    });

    wrap.addEventListener("click", function (e) {
      if (e.target.closest('[data-ctrl="bg"]')) {
        viewer.background = e.target.closest('[data-ctrl="bg"]').dataset.color;
      }
    });

    canvas.parentNode.insertBefore(wrap, canvas);
  }

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
    // 默认行走动画（对齐原版 animationFactories[0]）
    viewer.animation = new skinview3d.WalkingAnimation();
    // 暗色主题默认灰背景（原版 dark-mode 同款色）
    if (document.documentElement.dataset.bsTheme === "dark") {
      viewer.background = BG_GRAY;
    }
    buildControls(canvas, viewer, Boolean(options.cape));
    return viewer;
  };
})();
