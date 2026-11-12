"""3D 皮肤立绘渲染测试（移植 bs-community/texture-renderer）。"""

import io

from PIL import Image

from textures.skinrender import (
    SkinRenderer,
    render_cape_portrait,
    render_skin_portrait,
)


def skin_bytes(size=(64, 32), solid=(255, 0, 0, 255)):
    img = Image.new("RGBA", size, solid)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def real_skin_bytes():
    """真实形态皮肤：头蓝、身红、腿绿（透明底，不触发背景清除）。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (32, 16), (0, 0, 255, 255)), (0, 0))
    img.paste(Image.new("RGBA", (56, 16), (255, 0, 0, 255)), (0, 16))
    img.paste(Image.new("RGBA", (32, 16), (0, 128, 0, 255)), (16, 48))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestSkinPortrait:
    def test_render_returns_png_bytes(self):
        out = render_skin_portrait(skin_bytes(), ratio=4)
        img = Image.open(io.BytesIO(out))
        assert img.format == "PNG"
        assert img.mode == "RGBA"

    def test_front_back_dual_view_dimensions(self):
        """正面+背面双视角拼合：宽必须大于单视角高度（带 padding）。"""
        out = render_skin_portrait(skin_bytes(), ratio=4)
        img = Image.open(io.BytesIO(out))
        assert img.width > img.height  # 双视角 + padding
        assert img.height > 50  # ratio 4 → 非小图

    def test_portrait_has_colored_pixels(self):
        """真实形态皮肤渲染后输出应含不透明彩色像素。"""
        out = render_skin_portrait(real_skin_bytes(), ratio=4)
        img = Image.open(io.BytesIO(out)).convert("RGBA")
        opaque = sum(
            1
            for x in range(0, img.width, 2)
            for y in range(0, img.height, 2)
            if img.getpixel((x, y))[3] > 200
        )
        assert opaque > 100, f"不透明像素过少：{opaque}"

    def test_hd_skin_renders(self):
        assert render_skin_portrait(skin_bytes((128, 64)), ratio=4)

    def test_alex_skin_renders(self):
        assert render_skin_portrait(skin_bytes((64, 64)), ratio=4, is_alex=True)

    def test_new_skin_layers_merged(self):
        """1.8 双层皮肤（64x64）渲染不异常且输出有效。"""
        out = render_skin_portrait(skin_bytes((64, 64)), ratio=4)
        assert Image.open(io.BytesIO(out)).size[0] > 0

    def test_background_color_removal(self):
        """老皮肤含纯色背景 → 自动抠透明（角块纯色不透明检测）。"""
        img = Image.new("RGBA", (64, 32), (255, 255, 0, 255))  # 全黄背景
        img.paste(Image.new("RGBA", (64, 16), (0, 0, 255, 255)), (0, 16))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out = render_skin_portrait(buf.getvalue(), ratio=4)
        opened = Image.open(io.BytesIO(out)).convert("RGBA")
        yellow = sum(
            1
            for x in range(0, opened.width, 2)
            for y in range(0, opened.height, 2)
            if opened.getpixel((x, y))[:3] == (255, 255, 0)
            and opened.getpixel((x, y))[3] > 200
        )
        assert yellow == 0  # 背景黄被抠掉

    def test_cape_portrait(self):
        out = render_cape_portrait(skin_bytes((64, 32)), height=100)
        img = Image.open(io.BytesIO(out))
        assert img.height == 140  # 100 + vp*2（20*2）


class TestSkinRendererInternals:
    def test_single_view_aspect(self):
        renderer = SkinRenderer(4, False, -45)
        out = renderer.render(skin_bytes(), is_alex=False)
        assert out.width > 40 and out.height > 40

    def test_face_order_deterministic(self):
        r = SkinRenderer(4, False, -45)
        back, front = r._faces_order()
        assert "front" in front
        assert "back" in back
        r2 = SkinRenderer(4, False, 135)
        back2, _ = r2._faces_order()
        assert back2 != back  # 背面视角应反转


# ---- 1.8 外层合并（坐标须随 hd 缩放）----


RED = (255, 0, 0, 255)


def _paint(img, box, color):
    x0, y0, x1, y1 = box
    for x in range(x0, x1):
        for y in range(y0, y1):
            img.putpixel((x, y), color)


class TestOverlayMerge:
    def test_merge_sd_64(self):
        """64x64：右臂外层贴到右臂底层位，头部不受影响。"""
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        _paint(img, (40, 32, 56, 48), RED)  # 右臂外层
        SkinRenderer._merge_overlays(img, 1)
        assert img.getpixel((44, 20)) == RED  # 底层右臂 front 位被贴上
        assert img.getpixel((10, 10))[3] == 0  # 头部干净

    def test_merge_hd_128(self):
        """128x128：外层须映射到底层区域，而非头部（HD 错位主因）。"""
        img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        _paint(img, (80, 64, 112, 96), RED)  # 右臂外层 ×2
        SkinRenderer._merge_overlays(img, 2)
        assert img.getpixel((88, 40)) == RED  # 底层右臂 front 位（44,20）×2
        assert img.getpixel((20, 20))[3] == 0  # 头正面区（8,8）×2 修复前被污染
        assert img.getpixel((100, 20))[3] == 0  # 头右/帽层区不受影响


# ---- Alex 手臂 UV 公式（按 box 展开规则锁定）----


class TestAlexArmUV:
    """box UV：横向 outer(u-d) | front(u) | inner(u+w) | back(u+w+d)。

    右臂 front 锚 (44,20)：Alex(w=3,d=4) → outer[40,44) front[44,47)
    inner[47,51) back[51,54)。
    左臂 front 锚 (36,52)：outer[32,36) front[36,39) inner[39,43) back[43,46)。
    """

    def _alex_skin(self, size):
        img = Image.new("RGBA", (size, size), (10, 10, 10, 255))
        k = size // 64
        c = {
            "right_outer": (255, 0, 0, 255),    # (40,20) 起
            "right_front": (0, 255, 0, 255),    # (44,20)
            "right_inner": (0, 0, 255, 255),    # (47,20)
            "right_back": (255, 255, 0, 255),   # (51,20)
            "left_outer": (128, 0, 255, 255),   # (32,52)
            "left_front": (0, 255, 255, 255),   # (36,52)
            "left_inner": (255, 0, 255, 255),   # (39,52)
            "left_back": (255, 160, 0, 255),    # (43,52)
        }
        _paint(img, (40 * k, 20 * k, 44 * k, 32 * k), c["right_outer"])
        _paint(img, (44 * k, 20 * k, 47 * k, 32 * k), c["right_front"])
        _paint(img, (47 * k, 20 * k, 51 * k, 32 * k), c["right_inner"])
        _paint(img, (51 * k, 20 * k, 54 * k, 32 * k), c["right_back"])
        _paint(img, (32 * k, 52 * k, 36 * k, 64 * k), c["left_outer"])
        _paint(img, (36 * k, 52 * k, 39 * k, 64 * k), c["left_front"])
        _paint(img, (39 * k, 52 * k, 43 * k, 64 * k), c["left_inner"])
        _paint(img, (43 * k, 52 * k, 46 * k, 64 * k), c["left_back"])
        return img, c

    def _poly(self):
        return {
            p: {f: [] for f in ("back", "right", "top", "front", "left", "bottom")}
            for p in ("helmet", "head", "torso", "rightArm", "leftArm",
                      "rightLeg", "leftLeg")
        }

    def test_right_arm_faces_sd(self):
        img, c = self._alex_skin(64)
        r = SkinRenderer(4)
        poly = self._poly()
        r._gen_arm(poly, img.load(), 1, True, True, right=True)
        arm = poly["rightArm"]
        assert arm["front"][0].color == c["right_front"]   # px[44,20]
        assert arm["back"][0].color == c["right_back"]     # px[53,20] 翻转
        assert arm["right"][0].color == c["right_outer"]   # px[40,20]
        assert arm["left"][0].color == c["right_inner"]    # px[50,20] 翻转

    def test_right_arm_faces_hd(self):
        img, c = self._alex_skin(128)
        r = SkinRenderer(4)
        poly = self._poly()
        r._gen_arm(poly, img.load(), 2, True, True, right=True)
        arm = poly["rightArm"]
        assert arm["front"][0].color == c["right_front"]   # px[88,40]
        assert arm["back"][0].color == c["right_back"]     # px[107,40] 翻转
        assert arm["right"][0].color == c["right_outer"]   # px[80,40]
        assert arm["left"][0].color == c["right_inner"]    # px[101,40] 翻转

    def test_left_arm_inner_not_shifted(self):
        """Alex 左臂内侧取 [39,43) 翻转，不得吃进 back 首列（原 43 偏移）。"""
        img, c = self._alex_skin(64)
        r = SkinRenderer(4)
        poly = self._poly()
        r._gen_arm(poly, img.load(), 1, True, True, right=False)
        arm = poly["leftArm"]
        assert arm["front"][0].color == c["left_front"]    # px[36,52]
        assert arm["back"][0].color == c["left_back"]      # px[43,52]
        assert arm["right"][0].color == c["left_outer"]    # px[32,52]
        assert arm["left"][0].color == c["left_inner"]     # px[42,52] 翻转

    def test_alex_arm_box_width(self):
        """Alex 手臂盒宽 = 3*hd（内侧面与 front 面贴合，无悬空缝）。"""
        img, _ = self._alex_skin(64)
        r = SkinRenderer(4)
        poly = self._poly()
        r._gen_arm(poly, img.load(), 1, True, True, right=True)
        # right face 挂 x=ox=-3；left face 挂 x=ox+aw=0（紧贴躯干）
        right_x = {p.pts[0].x for p in poly["rightArm"]["right"]}
        left_x = {p.pts[0].x for p in poly["rightArm"]["left"]}
        assert right_x == {-3.0}
        assert left_x == {0.0}


# ---- HD 渲染与 SD 基线一致性（错位回归）----


class TestHDRenderParity:
    def _rich_skin(self, size):
        """多区域彩色皮肤：底层分部位 + 外层独立色，检验整体渲染结构。"""
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        k = size // 64
        _paint(img, (8 * k, 8 * k, 16 * k, 16 * k), (0, 0, 255, 255))    # 头 front 蓝
        _paint(img, (20 * k, 20 * k, 28 * k, 32 * k), (255, 0, 0, 255))  # 躯干 front 红
        _paint(img, (4 * k, 20 * k, 8 * k, 32 * k), (0, 128, 0, 255))    # 右腿 front 绿
        _paint(img, (44 * k, 20 * k, 48 * k, 32 * k), (255, 0, 255, 255))  # 右臂 front 洋红
        # 外层（躯干/臂 overlay 独立色，考验层合并位置）
        _paint(img, (20 * k, 36 * k, 28 * k, 44 * k), (255, 128, 0, 255))  # 躯干外层橙
        _paint(img, (44 * k, 36 * k, 48 * k, 44 * k), (0, 255, 255, 255))  # 右臂外层青
        return img

    def test_hd_matches_sd_baseline(self):
        """128 皮肤（SD 逐像素放大）渲染应与 64 基线几乎一致。

        比较单视角渲染（portrait 拼合的 padding 是常量，不随 hd 缩放，
        纵横比不同不可直接对齐）。修复前：外层贴到头部区 → 差异显著。
        """
        from PIL import ImageChops, ImageStat

        sd_img = self._rich_skin(64)
        hd_img = sd_img.resize((128, 128), Image.NEAREST)

        sd_buf, hd_buf = io.BytesIO(), io.BytesIO()
        sd_img.save(sd_buf, format="PNG")
        hd_img.save(hd_buf, format="PNG")

        for rot in (-45, 135):  # 正面 + 背面视角
            a = (
                SkinRenderer(6, False, rot)
                .render(sd_buf.getvalue(), is_alex=False)
                .resize((96, 96), Image.LANCZOS)
                .convert("RGBA")
            )
            b = (
                SkinRenderer(6, False, rot)
                .render(hd_buf.getvalue(), is_alex=False)
                .resize((96, 96), Image.LANCZOS)
                .convert("RGBA")
            )
            stat = ImageStat.Stat(ImageChops.difference(a, b))
            for mean in stat.mean:
                assert mean < 8.0, (
                    f"HD 与 SD 基线差异过大（视角 {rot}）：{stat.mean}"
                )
