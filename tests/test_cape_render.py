"""披风 2D 预览渲染测试：像素画缩放须邻近采样（NEAREST）。

LANCZOS 平滑插值会把像素色块糊成渐变——像素游戏预览应保持锐利
（GD imagecopyresized 无平滑 = NEAREST 语义，忠实原版）。
"""

import io

import pytest
from PIL import Image

from textures.skinrender import render_cape_portrait

pytestmark = pytest.mark.django_db


def _checker_cape() -> bytes:
    """64x32 标准披风，裁剪区画满红蓝棋盘（2 像素格）。"""
    cape = Image.new("RGBA", (64, 32), (0, 0, 0, 255))
    for x in range(1, 11):
        for y in range(1, 17):
            color = (
                (255, 0, 0, 255) if (x // 2 + y // 2) % 2 == 0 else (0, 0, 255, 255)
            )
            cape.putpixel((x, y), color)
    buf = io.BytesIO()
    cape.save(buf, format="PNG")
    return buf.getvalue()


class TestCapePortraitNearestScaling:
    def test_no_intermediate_colors(self):
        """棋盘格放大后只有纯红/纯蓝（LANCZOS 会产生大量中间色）。"""
        out = Image.open(io.BytesIO(render_cape_portrait(_checker_cape(), 200)))
        colors = {
            (255, 0, 0, 255),
            (0, 0, 255, 255),
            (0, 0, 0, 0),  # 画布透明底
        }
        seen = set()
        for px in out.getdata():
            seen.add(px)
        unexpected = seen - colors
        assert not unexpected, f"出现插值中间色（应仅纯色）：{list(unexpected)[:5]}"

    def test_pixel_edges_stay_sharp(self):
        """放大后色块边界仍为硬边（采样列上无渐变过渡行）。"""
        out = Image.open(io.BytesIO(render_cape_portrait(_checker_cape(), 320)))
        center_row = [
            out.getpixel((x, out.height // 2)) for x in range(out.width)
        ]
        transition = [
            (a, b) for a, b in zip(center_row, center_row[1:], strict=False)
            if a != b
        ]
        assert transition  # 存在色彩跳变（全平 = 没渲染）
        # 每处跳变的两侧都应是纯色，而非接近色
        for a, b in transition:
            for c in (a, b):
                assert c in {(255, 0, 0, 255), (0, 0, 255, 255), (0, 0, 0, 0)}, c
