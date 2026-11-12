"""Yggdrasil P2/P3 协议对齐回归（定向）。

覆盖审查清单中 P2/P3 级修复项，每项配锁定测试：
- P2-26: setup 生成签名密钥 4096 位
- P2-21: 披风 22x17 遗留尺寸（透明补足至 64x32 倍数）
- P3-31: accessToken JWT 补 spr claim
- P3-30: 日志写库受 YGG_VERBOSE_LOG 开关控制
- P3-34: 令牌 access_token 唯一约束
- P3-32: authenticate 时清理过期令牌行
- P2-24: 面板 Yggdrasil 日志页
- P2-23: 面板私钥编辑/重新生成
- P2-25: 上传材质计分/衣柜/命名（对照 ProfileController.php）
- P2-20: 材质 hash 像素级 SHA-256（规范 L243-254）
"""

import io
import json

import pytest
from PIL import Image

from accounts.models import User
from players.models import Player

pytestmark = pytest.mark.django_db

BASE = "/api/yggdrasil"


def png_bytes(size, color=(255, 0, 0, 255)) -> bytes:
    """生成指定尺寸的纯色 PNG。"""
    img = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def seeded(db):
    user = User.objects.create_user(
        email="y@x.com", nickname="y", password="password8", verified=True
    )
    Player.objects.create(uid=user, name="Steve")
    return user


def authenticate(client, **extra):
    resp = client.post(
        BASE + "/authserver/authenticate",
        data=json.dumps({"username": "y@x.com", "password": "password8", **extra}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    return resp.json()


# ---- P2-26: setup 密钥 4096 位 ----


class TestP2SetupKeySize:
    def test_setup_generates_4096_bit_key(self, db):
        from setup.views import _generate_ygg_keypair

        _generate_ygg_keypair()
        from cryptography.hazmat.primitives import serialization

        from options.models import Option

        pem = Option.objects.get_value("ygg_private_key")
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        assert key.key_size == 4096


# ---- P2-21: 披风 22x17 遗留比例（规范 L395）----


class TestP2CapeLegacy22x17:
    def test_22x17_padded_to_64x32(self):
        from textures.validation import validate_texture_image

        result = validate_texture_image(
            png_bytes((22, 17), color=(0, 255, 0, 255)), "cape"
        )
        assert result.valid
        assert (result.width, result.height) == (64, 32)
        img = Image.open(io.BytesIO(result.normalized))
        assert img.size == (64, 32)
        assert img.getpixel((0, 0)) == (0, 255, 0, 255)  # 原内容保留
        assert img.getpixel((63, 31)) == (0, 0, 0, 0)  # 补足区透明

    def test_44x34_padded_to_64x64(self):
        from textures.validation import validate_texture_image

        result = validate_texture_image(png_bytes((44, 34)), "cape")
        assert result.valid
        assert (result.width, result.height) == (64, 64)

    def test_mixed_multiplier_rejected(self):
        """宽 2 倍、高 1 倍（44x17）不是 22x17 的整数倍 → 拒绝。"""
        from textures.validation import validate_texture_image

        result = validate_texture_image(png_bytes((44, 17)), "cape")
        assert not result.valid

    def test_ratio2_non_multiple_now_rejected(self):
        """92x46 虽为精确 2:1，但非 64x32 倍数 → 按规范拒绝。"""
        from textures.validation import validate_texture_image

        result = validate_texture_image(png_bytes((92, 46)), "cape")
        assert not result.valid

    def test_standard_64x32_still_valid(self):
        from textures.validation import validate_texture_image

        result = validate_texture_image(png_bytes((64, 32)), "cape")
        assert result.valid


# ---- P3-31: JWT 补 spr claim（对照 AuthController.php:63/159/171）----


class TestP3SprClaim:
    def _decode(self, token: str) -> dict:
        from yggdrasil.tokens import decode_access_token

        claims = decode_access_token(token)
        assert claims is not None
        return claims

    def test_spr_present_when_profile_bound(self, client, seeded):
        tokens = authenticate(client)
        claims = self._decode(tokens["accessToken"])
        assert claims["spr"] == tokens["selectedProfile"]["id"]
        assert claims["iss"] == "Yggdrasil-Auth"
        assert claims["yggt"]

    def test_spr_absent_without_profiles(self, client, db):
        User.objects.create_user(
            email="n@x.com", nickname="n", password="password8", verified=True
        )
        resp = client.post(
            BASE + "/authserver/authenticate",
            data=json.dumps({"username": "n@x.com", "password": "password8"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        claims = self._decode(resp.json()["accessToken"])
        assert "spr" not in claims

    def test_refresh_carries_spr(self, client, seeded):
        tokens = authenticate(client)
        resp = client.post(
            BASE + "/authserver/refresh",
            data=json.dumps({
                "accessToken": tokens["accessToken"],
                "clientToken": tokens["clientToken"],
            }),
            content_type="application/json",
        )
        assert resp.status_code == 200
        claims = self._decode(resp.json()["accessToken"])
        assert claims["spr"] == tokens["selectedProfile"]["id"]
