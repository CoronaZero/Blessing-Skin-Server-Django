"""纹理渲染：2D 头像与纹理响应（Pillow）。

对照原版 blessing/texture-renderer 的 2D 部分：
64x32 皮肤面部在 (8,8)；64x64 在 (8,8)（带头层仍取底层面部）；
HD（128x64 等）按 scale 放大取面部区域。
"""

import io

from PIL import Image

from .storage import texture_storage

# 头像支持的尺寸变体（对照原版 avatar/{size}）
AVATAR_SIZES = (32, 64, 128, 180, 256)


def render_avatar(skin_bytes: bytes, size: int, overlay: bool = True) -> bytes:
    """渲染头像：面部 8x8 区域放大至 size×size。

    overlay=True 时叠加头层（半透明帽层覆盖，对照原版）。
    """
    img = Image.open(io.BytesIO(skin_bytes))
    width, height = img.size
    scale = width // 64  # HD 皮肤 scale（64x32 → 1, 128x64 → 2）

    # 1:1 / 2:1 皮肤面部基线均取 (8, 8)
    face_x, face_y = 8 * scale, 8 * scale
    face_size = 8 * scale

    face = img.crop((face_x, face_y, face_x + face_size, face_y + face_size))
    face = face.resize((size, size), Image.NEAREST)

    if overlay and height >= width:  # 有头层（1:1 皮肤或 64x64）
        # 第二层面部区域 (40,8)；64x32 无头层则跳过
        hat_x = 40 * scale
        if hat_x + face_size <= width:
            hat = img.crop((hat_x, face_y, hat_x + face_size, face_y + face_size))
            hat = hat.resize((size, size), Image.NEAREST)
            # 仅叠加非全透明像素（真实皮肤的帽层大部分区域为透明）
            alpha = hat.getchannel("A")
            if alpha.getextrema()[0] < 255:  # 存在非不透明像素才叠加
                face = Image.alpha_composite(face.convert("RGBA"), hat.convert("RGBA"))

    buf = io.BytesIO()
    face.save(buf, format="PNG")
    return buf.getvalue()


def render_texture_response(hash_value: str) -> tuple[bytes, dict]:
    """纹理文件响应体 + 缓存头（ETag = hash，强缓存）。"""
    from django.conf import settings

    data = texture_storage.read(hash_value)
    max_age = getattr(settings, "TEXTURE_CACHE_MAX_AGE", 86400)
    headers = {
        "Content-Type": "image/png",
        "ETag": f'"{hash_value}"',
        "Cache-Control": f"public, max-age={max_age}",
    }
    return data, headers
