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
