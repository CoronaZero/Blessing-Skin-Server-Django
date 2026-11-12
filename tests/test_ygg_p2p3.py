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


# ---- P3-30: 日志写库受 YGG_VERBOSE_LOG 开关控制（helpers.php:47）----


class TestP3VerboseLog:
    def test_no_log_by_default(self, client, seeded):
        from yggdrasil.models import YggdrasilLog

        authenticate(client)
        assert YggdrasilLog.objects.count() == 0

    def test_log_written_when_enabled(self, client, seeded, settings):
        from yggdrasil.models import YggdrasilLog

        settings.YGG_VERBOSE_LOG = True
        tokens = authenticate(client)
        assert YggdrasilLog.objects.filter(action="authenticate").exists()

        # join / has_joined 亦记录
        resp = client.post(
            BASE + "/sessionserver/session/minecraft/join",
            data=json.dumps({
                "accessToken": tokens["accessToken"],
                "selectedProfile": tokens["selectedProfile"]["id"],
                "serverId": "server",
            }),
            content_type="application/json",
        )
        assert resp.status_code == 204
        assert YggdrasilLog.objects.filter(action="join").exists()


# ---- P3-34: 令牌行每次新建 + access_token 唯一约束 ----


class TestP3TokenUnique:
    def test_duplicate_access_token_rejected(self, db, seeded):
        from django.db import IntegrityError, transaction

        from yggdrasil.models import YggdrasilToken

        YggdrasilToken.objects.create(
            owner=seeded, client_token="c1", access_token="tok",
            expires_in=1, refresh_expires_in=2,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                YggdrasilToken.objects.create(
                    owner=seeded, client_token="c2", access_token="tok",
                    expires_in=1, refresh_expires_in=2,
                )

    def test_authenticate_creates_fresh_row(self, client, seeded):
        from yggdrasil.models import YggdrasilToken

        authenticate(client)
        authenticate(client)
        assert YggdrasilToken.objects.count() == 2


# ---- P3-32: 过期令牌行清理 ----


class TestP3ExpiredCleanup:
    def test_dead_rows_purged_refreshable_kept(self, client, seeded):
        from datetime import timedelta

        from django.utils import timezone

        from yggdrasil.models import YggdrasilToken

        dead = YggdrasilToken.objects.create(
            owner=seeded, client_token="old", access_token="dead-tok",
            expires_in=10, refresh_expires_in=20,
        )
        dead.created_at = timezone.now() - timedelta(seconds=3600)
        dead.save()
        # 已过有效期但仍在刷新窗口内 → 保留（可 refresh 续期）
        YggdrasilToken.objects.create(
            owner=seeded, client_token="new", access_token="alive-tok",
            expires_in=10, refresh_expires_in=604800,
        )

        authenticate(client)

        assert not YggdrasilToken.objects.filter(access_token="dead-tok").exists()
        assert YggdrasilToken.objects.filter(access_token="alive-tok").exists()


# ---- P2-24: 面板 Yggdrasil 日志页（仅超管）----


class TestP2PanelYggLogPage:
    def _super_client(self, client):
        User.objects.create_user(
            email="sp@x.com", nickname="sp", password="password8",
            permission=User.Permission.SUPER_ADMIN, verified=True,
        )
        client.login(username="sp@x.com", password="password8")
        return client

    def test_super_views_log_page(self, client, db):
        from yggdrasil.models import YggdrasilLog

        c = self._super_client(client)
        YggdrasilLog.objects.create(action="authenticate", parameters="{}")
        resp = c.get("/admin/yggdrasil/logs")
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "Yggdrasil 日志" in html
        assert "authenticate" in html

    def test_filter_by_action(self, client, db):
        from yggdrasil.models import YggdrasilLog

        c = self._super_client(client)
        YggdrasilLog.objects.create(action="join", parameters='{"serverId": "srv-x"}')
        YggdrasilLog.objects.create(action="signout", parameters="BYE-MARKER")
        html = c.get("/admin/yggdrasil/logs?action=join").content.decode()
        assert "加入服务器" in html
        # 筛除 signout 行（下拉选项含全部动作文案，故用行内独有标记断言）
        assert "srv-x" in html
        assert "BYE-MARKER" not in html

    def test_plain_admin_gets_404(self, client, db):
        User.objects.create_user(
            email="ad@x.com", nickname="ad", password="password8",
            permission=User.Permission.ADMIN, verified=True,
        )
        client.login(username="ad@x.com", password="password8")
        assert client.get("/admin/yggdrasil/logs").status_code == 404

    def test_sidebar_entry_super_only(self, client, db):
        c = self._super_client(client)
        html = c.get("/admin/").content.decode()
        assert "/admin/yggdrasil/logs" in html


# ---- P2-23: 面板私钥编辑/重新生成（对照 ConfigController.php:41-60/generate）----


class TestP2PanelKeyManagement:
    def _super_client(self, client):
        User.objects.create_user(
            email="sp@x.com", nickname="sp", password="password8",
            permission=User.Permission.SUPER_ADMIN, verified=True,
        )
        client.login(username="sp@x.com", password="password8")
        return client

    def _pem(self, bits: int = 2048) -> str:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
        return key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()

    def test_generate_rotates_keypair(self, client, db):
        from cryptography.hazmat.primitives import serialization

        from options.models import Option

        c = self._super_client(client)
        resp = c.post("/admin/options/ygg-key/generate")
        assert resp.json()["code"] == 0
        pem = Option.objects.get_value("ygg_private_key")
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        assert key.key_size == 4096
        # 公钥 option 同步派生
        pub = Option.objects.get_value("ygg_public_key")
        assert pub.startswith("-----BEGIN PUBLIC KEY-----")

    def test_save_custom_key_derives_public(self, client, db):
        from options.models import Option

        c = self._super_client(client)
        pem = self._pem()
        resp = c.post("/admin/options/save", {"ygg_private_key": pem})
        assert resp.json()["code"] == 0
        # 保存链路对文本统一 strip，PEM 尾换行不保留（不影响加载）
        assert Option.objects.get_value("ygg_private_key") == pem.strip()
        assert Option.objects.get_value("ygg_public_key").startswith(
            "-----BEGIN PUBLIC KEY-----"
        )

    def test_invalid_pem_rejected(self, client, db):
        from options.models import Option

        c = self._super_client(client)
        resp = c.post("/admin/options/save", {"ygg_private_key": "not-a-pem"})
        assert resp.json()["code"] != 0
        assert Option.objects.get_value("ygg_private_key") in (None, "")

    def test_empty_save_keeps_existing_key(self, client, db):
        from options.models import Option

        c = self._super_client(client)
        Option.objects.set("ygg_private_key", "PEM-PLACEHOLDER")
        resp = c.post("/admin/options/save", {"ygg_private_key": ""})
        assert resp.json()["code"] == 0
        assert Option.objects.get_value("ygg_private_key") == "PEM-PLACEHOLDER"

    def test_options_page_shows_key_bits(self, client, db):
        from options.models import Option

        c = self._super_client(client)
        Option.objects.set("ygg_private_key", self._pem())
        html = c.get("/admin/options/yggdrasil").content.decode()
        assert "2048 位" in html
        assert "重新生成 4096 位密钥对" in html


# ---- P2-25: 上传材质计分/衣柜/命名（对照 ProfileController.php:100-203）----


def _storage_tmp(tmp_path, settings):
    from django.core.files.storage import FileSystemStorage

    settings.TEXTURE_STORAGE_ROOT = str(tmp_path / "t")
    from textures import storage as sm

    sm.texture_storage._storage = FileSystemStorage(
        location=settings.TEXTURE_STORAGE_ROOT
    )


class TestP2UploadScoring:
    def _multipart(self, png: bytes, filename: str):
        boundary = "BSSP2Boundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode() + png + f"\r\n--{boundary}--\r\n".encode()
        return body, f"multipart/form-data; boundary={boundary}"

    def _upload(self, client, tokens, uuid, png, filename="skin.png"):
        body, ctype = self._multipart(png, filename)
        return client.put(
            f"{BASE}/api/user/profile/{uuid}/skin",
            data=body,
            content_type=ctype,
            headers={"Authorization": f"Bearer {tokens['accessToken']}"},
        )

    def test_new_upload_scores_and_closets(self, client, seeded, tmp_path, settings):
        from options.models import Option

        _storage_tmp(tmp_path, settings)
        Option.objects.set("private_score_per_storage", "2")
        Option.objects.set("score_per_closet_item", "3")
        seeded.score = 100
        seeded.save()
        tokens = authenticate(client)
        uuid = tokens["selectedProfile"]["id"]

        resp = self._upload(client, tokens, uuid, png_bytes((64, 32)), "my_skin.png")
        assert resp.status_code == 204, resp.content.decode()[:200]

        from textures.models import ClosetItem, Texture

        texture = Texture.objects.get(name="my_skin")
        assert texture.likes == 1  # 上传即自收藏（原版 L189）
        assert texture.public is False
        assert ClosetItem.objects.filter(
            owner=seeded, texture=texture, item_name="my_skin"
        ).exists()
        seeded.refresh_from_db()
        assert seeded.score == 95  # 存储费 2*1KB + 衣柜费 3

    def test_insufficient_score_403(self, client, seeded, tmp_path, settings):
        from options.models import Option

        _storage_tmp(tmp_path, settings)
        Option.objects.set("private_score_per_storage", "2")
        seeded.score = 1
        seeded.save()
        tokens = authenticate(client)
        uuid = tokens["selectedProfile"]["id"]

        resp = self._upload(client, tokens, uuid, png_bytes((64, 32)))
        assert resp.status_code == 403
        assert "积分不足" in resp.json()["errorMessage"]

        from textures.models import Texture

        assert not Texture.objects.exists()
        seeded.refresh_from_db()
        assert seeded.score == 1

    def test_duplicate_readds_closet_without_charge(
        self, client, seeded, tmp_path, settings
    ):
        from options.models import Option

        _storage_tmp(tmp_path, settings)
        Option.objects.set("score_per_closet_item", "3")
        seeded.score = 10
        seeded.save()
        tokens = authenticate(client)
        uuid = tokens["selectedProfile"]["id"]
        png = png_bytes((64, 32))

        assert self._upload(client, tokens, uuid, png, "first.png").status_code == 204
        seeded.refresh_from_db()
        assert seeded.score == 7

        # 移出衣柜后重新上传同图：复用材质行，补入衣柜但不扣分（原版 L164-172）
        from textures.models import ClosetItem

        ClosetItem.objects.filter(owner=seeded).delete()
        resp = self._upload(client, tokens, uuid, png, "second.png")
        assert resp.status_code == 204
        from textures.models import Texture

        assert Texture.objects.count() == 1
        # 补入衣柜用本次文件名去 .png（原版 replaceLast）
        assert ClosetItem.objects.get(owner=seeded).item_name == "second"
        seeded.refresh_from_db()
        assert seeded.score == 7

    def test_raw_body_name_falls_back_to_player(
        self, client, seeded, tmp_path, settings
    ):
        _storage_tmp(tmp_path, settings)
        tokens = authenticate(client)
        uuid = tokens["selectedProfile"]["id"]
        resp = client.put(
            f"{BASE}/api/user/profile/{uuid}/skin",
            data=png_bytes((64, 32)),
            content_type="image/png",
            headers={"Authorization": f"Bearer {tokens['accessToken']}"},
        )
        assert resp.status_code == 204
        from textures.models import Texture

        assert Texture.objects.get().name == "Steve"


# ---- P2-20: 材质 hash 像素级 SHA-256（规范 L243-254）----


class TestP2PixelHash:
    def test_known_vector_1x2(self):
        """缓冲区 = header(width,height 大端) + 列主序 A/R/G/B 像素字节。"""
        import hashlib
        import struct

        from textures.validation import _pixel_digest

        img = Image.new("RGBA", (1, 2))
        img.putpixel((0, 0), (255, 0, 0, 255))  # → A,R,G,B = 255,255,0,0
        img.putpixel((0, 1), (0, 0, 255, 128))  # → 128,0,0,255
        buf = struct.pack(">II", 1, 2) + bytes([255, 255, 0, 0, 128, 0, 0, 255])
        assert _pixel_digest(img) == hashlib.sha256(buf).hexdigest()

    def test_column_major_order_2x2(self):
        """像素按 x 外层、y 内层排列（规范 L248 offset 公式）。"""
        import hashlib
        import struct

        from textures.validation import _pixel_digest

        img = Image.new("RGBA", (2, 2))
        img.putpixel((0, 0), (255, 0, 0, 255))  # 红
        img.putpixel((1, 0), (0, 255, 0, 255))  # 绿
        img.putpixel((0, 1), (0, 0, 255, 255))  # 蓝
        img.putpixel((1, 1), (255, 255, 255, 255))
        argb = {
            (0, 0): bytes([255, 255, 0, 0]),
            (0, 1): bytes([255, 0, 0, 255]),
            (1, 0): bytes([255, 0, 255, 0]),
            (1, 1): bytes([255, 255, 255, 255]),
        }
        buf = struct.pack(">II", 2, 2) + b"".join(
            argb[(x, y)] for x in range(2) for y in range(2)
        )
        assert _pixel_digest(img) == hashlib.sha256(buf).hexdigest()

    def test_transparent_rgb_zeroed(self):
        from textures.validation import _pixel_digest

        opaque = Image.new("RGBA", (64, 32), (255, 0, 0, 0))  # 全透明红
        other = Image.new("RGBA", (64, 32), (0, 255, 0, 0))  # 全透明绿
        assert _pixel_digest(opaque) == _pixel_digest(other)

    def test_same_pixels_different_encoding_same_digest(self):
        """同像素不同 PNG 编码（元数据差异）→ 同 hash，规范核心动机。"""
        from PIL.PngImagePlugin import PngInfo

        from textures.validation import validate_texture_image

        img = Image.new("RGBA", (64, 32), (7, 8, 9, 255))
        b1, b2 = io.BytesIO(), io.BytesIO()
        img.save(b1, format="PNG")
        meta = PngInfo()
        meta.add_text("comment", "different-encoding")
        img.save(b2, format="PNG", pnginfo=meta)
        assert b1.getvalue() != b2.getvalue()

        r1 = validate_texture_image(b1.getvalue(), "steve")
        r2 = validate_texture_image(b2.getvalue(), "steve")
        assert r1.valid and r2.valid
        assert r1.digest == r2.digest

    def test_upload_dedup_across_encodings(self, client, seeded, tmp_path, settings):
        _storage_tmp(tmp_path, settings)
        tokens = authenticate(client)
        uuid = tokens["selectedProfile"]["id"]

        img = Image.new("RGBA", (64, 32), (7, 8, 9, 255))
        b1, b2 = io.BytesIO(), io.BytesIO()
        img.save(b1, format="PNG")
        from PIL.PngImagePlugin import PngInfo

        meta = PngInfo()
        meta.add_text("comment", "different-encoding")
        img.save(b2, format="PNG", pnginfo=meta)

        headers = {"Authorization": f"Bearer {tokens['accessToken']}"}
        url = f"{BASE}/api/user/profile/{uuid}/skin"
        assert client.put(
            url, data=b1.getvalue(), content_type="image/png", headers=headers
        ).status_code == 204
        assert client.put(
            url, data=b2.getvalue(), content_type="image/png", headers=headers
        ).status_code == 204

        from textures.models import Texture

        # 不同编码同像素 → 复用同一材质行
        assert Texture.objects.count() == 1
