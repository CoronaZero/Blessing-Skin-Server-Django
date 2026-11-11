"""纹理存储与校验测试（TDD 先行）。

对照 PHP 原版：
- 存储以 sha256 命名，无扩展名，直接放 root 目录
- 上传校验链：PNG MIME → 尺寸（宽 64 倍数/高 32 倍数）→ 长宽比
  （steve/alex：2 或 1，alex 不允许 2；cape：2）→ 重编码消毒（Pillow
  re-encode 去元数据/潜在恶意负载）→ hash 去重
- 头像：8x8 面部区域放大为指定尺寸（avatar/{size}/{player}.png）
"""

import io

import pytest
from PIL import Image

from textures.rendering import render_avatar, render_texture_response
from textures.storage import TextureFileNotFoundError, texture_storage
from textures.validation import (
    ValidationResult,
    validate_texture_image,
)


def png_bytes(size, color=(255, 0, 0, 255)) -> bytes:
    """生成指定尺寸的纯色 PNG。"""
    img = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


VALID_SKIN_64_32 = png_bytes((64, 32))
VALID_SKIN_64_64 = png_bytes((64, 64))
VALID_CAPE_64_32 = png_bytes((64, 32), color=(0, 255, 0, 255))


@pytest.fixture(autouse=True)
def _use_tmp_storage(tmp_path, settings):
    from django.core.files.storage import FileSystemStorage

    settings.TEXTURE_STORAGE_ROOT = str(tmp_path / "textures")
    texture_storage._storage = FileSystemStorage(location=settings.TEXTURE_STORAGE_ROOT)


# ---- 校验 ----


class TestValidation:
    def test_valid_steve_skin_64x32(self):
        result = validate_texture_image(VALID_SKIN_64_32, "steve")
        assert result.valid
        assert result.normalized  # 消毒后字节

    def test_valid_hd_skin_128x64(self):
        result = validate_texture_image(png_bytes((128, 64)), "steve")
        assert result.valid

    def test_alex_ratio_2_rejected(self):
        """alex 不允许 64x32（原版 alex 必须 1:1）。"""
        result = validate_texture_image(png_bytes((64, 32)), "alex")
        assert not result.valid
        assert "长宽比" in result.error

    def test_invalid_ratio(self):
        result = validate_texture_image(png_bytes((100, 100)), "steve")
        assert not result.valid

    def test_invalid_multiple(self):
        # 65x32: 宽度不是 64 倍数
        result = validate_texture_image(png_bytes((65, 32)), "steve")
        assert not result.valid

    def test_cape_valid(self):
        """cape 需精确 2:1（原版及 yggdrasil-connect 均如此）。

        标准 Minecraft cape 46x22（≈ 2.09）同样会被原版拒绝，
        仅接受 64x32 类精确比例，此处对齐原版行为。
        """
        result = validate_texture_image(VALID_CAPE_64_32, "cape")
        assert result.valid

    def test_cape_standard_46_22_rejected_like_php(self):
        """46x22 非精确 2:1，原版 PHP 亦拒绝（行为对齐锚点）。"""
        result = validate_texture_image(png_bytes((46, 22)), "cape")
        assert not result.valid
        assert "披风" in result.error

    def test_cape_invalid_ratio(self):
        # 46x30 ≈ 1.53，不是 2:1
        result = validate_texture_image(png_bytes((46, 30)), "cape")
        assert not result.valid

    def test_not_png_rejected(self):
        result = validate_texture_image(b"GIF89a" + b"\x00" * 100, "steve")
        assert not result.valid

    def test_corrupt_png_rejected(self):
        result = validate_texture_image(b"\x89PNG\r\n\x1a\n" + b"garbage", "steve")
        assert not result.valid

    def test_sanitization_strips_metadata(self):
        """消毒后的图像不含文本元数据块。"""
        img = Image.new("RGBA", (64, 32))
        info = img.info
        info["comment"] = "evil payload"
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()

        result = validate_texture_image(raw, "steve")
        assert result.valid
        reloaded = Image.open(io.BytesIO(result.normalized))
        assert "comment" not in reloaded.info


# ---- 存储 ----


class TestStorage:
    def test_save_and_exists(self):
        digest = "a" * 64
        texture_storage.save_bytes(digest, VALID_SKIN_64_32)
        assert texture_storage.exists(digest)

    def test_read_roundtrip(self):
        digest = "b" * 64
        texture_storage.save_bytes(digest, VALID_SKIN_64_32)
        data = texture_storage.read(digest)
        assert data == VALID_SKIN_64_32

    def test_delete(self):
        digest = "c" * 64
        texture_storage.save_bytes(digest, VALID_SKIN_64_32)
        texture_storage.delete(digest)
        assert not texture_storage.exists(digest)

    def test_read_missing_raises(self):
        with pytest.raises(TextureFileNotFoundError):
            texture_storage.read("d" * 64)

    def test_hash_is_only_filename(self):
        """文件名严格为 hex hash，无扩展名无路径穿越。

        跨平台断言：basename 恰好等于 digest（Windows '\' 与 Linux '/'
        路径分隔符均由 pathlib 归一处理）。
        """
        from pathlib import Path

        digest = "e" * 64
        texture_storage.save_bytes(digest, VALID_SKIN_64_32)
        stored_path = texture_storage.path(digest)
        assert Path(stored_path).name == digest


# ---- 头像渲染 ----


class TestAvatarRendering:
    def _make_skin(self) -> bytes:
        """64x32 皮肤，面部区域 (8,8,16,16) 标记为蓝色。"""
        img = Image.new("RGBA", (64, 32), (255, 0, 0, 255))
        face = Image.new("RGBA", (8, 8), (0, 0, 255, 255))
        img.paste(face, (8, 8))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_avatar_size_variants(self):
        skin = self._make_skin()
        for size in (32, 64, 128, 256):
            avatar = render_avatar(skin, size)
            img = Image.open(io.BytesIO(avatar))
            assert img.size == (size, size)

    def test_avatar_from_face_pixels(self):
        """头像取面部像素（64x32 皮肤: (8,8) 起 8x8）。"""
        skin = self._make_skin()
        avatar = render_avatar(skin, 64)
        img = Image.open(io.BytesIO(avatar)).convert("RGBA")
        # 中心像素应为蓝色（面部）
        assert img.getpixel((32, 32)) == (0, 0, 255, 255)

    def test_avatar_from_hd_skin(self):
        """128x64 HD 皮肤面部在 (16,16) 起 16x16。"""
        img = Image.new("RGBA", (128, 64), (255, 0, 0, 255))
        face = Image.new("RGBA", (16, 16), (0, 0, 255, 255))
        img.paste(face, (16, 16))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        avatar = render_avatar(buf.getvalue(), 64)
        out = Image.open(io.BytesIO(avatar)).convert("RGBA")
        assert out.getpixel((32, 32)) == (0, 0, 255, 255)

    def test_avatar_square_64_64_uses_8_8_offset(self):
        """64x64（带头层）皮肤面部仍在 (8,8)；透明帽层不遮挡底层面部。"""
        img = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
        face = Image.new("RGBA", (8, 8), (0, 0, 255, 255))
        img.paste(face, (8, 8))
        # 帽层区域 (40,8) 为全透明（真实皮肤语义）
        hat_zone = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        img.paste(hat_zone, (40, 8))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        avatar = render_avatar(buf.getvalue(), 64)
        out = Image.open(io.BytesIO(avatar)).convert("RGBA")
        assert out.getpixel((32, 32)) == (0, 0, 255, 255)

    def test_avatar_overlay_blends_semitransparent_hat(self):
        """半透明帽层叠加：输出为面部与帽层的混合色。"""
        img = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
        face = Image.new("RGBA", (8, 8), (0, 0, 255, 255))
        img.paste(face, (8, 8))
        # 帽层：50% 透明白色
        hat = Image.new("RGBA", (8, 8), (255, 255, 255, 128))
        img.paste(hat, (40, 8))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        avatar = render_avatar(buf.getvalue(), 64)
        out = Image.open(io.BytesIO(avatar)).convert("RGBA")
        r, g, b, a = out.getpixel((32, 32))
        # 蓝色面部与 50% 白帽混合 → 偏亮蓝
        assert a == 255 and b > 128 and g > 32


# ---- 纹理响应（Cache 头）----


class TestTextureResponse:
    def test_etag_present(self):
        digest = "f" * 64
        texture_storage.save_bytes(digest, VALID_SKIN_64_32)
        png, headers = render_texture_response(digest)
        assert headers["ETag"] == f'"{digest}"'
        assert headers["Content-Type"] == "image/png"

    def test_cache_control_configurable(self, settings):
        settings.TEXTURE_CACHE_MAX_AGE = 86400
        digest = "1" * 64
        texture_storage.save_bytes(digest, VALID_SKIN_64_32)
        _, headers = render_texture_response(digest)
        assert "max-age=86400" in headers["Cache-Control"]


# ---- ValidationResult 结构 ----


class TestValidationResult:
    def test_shape(self):
        ok = ValidationResult(valid=True, normalized=b"png", digest="a" * 64, width=64, height=32)
        assert ok.digest == "a" * 64
