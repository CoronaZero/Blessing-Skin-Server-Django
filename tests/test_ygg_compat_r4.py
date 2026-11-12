"""Yggdrasil 兼容性第四轮修复回归（静态审查报告核实修复）。

- P0-1: refresh 选角后 join（profile_id 漏存修复）
- P0-2: hello 无密钥时惰性生成 4096 密钥对（禁止空串 signaturePublickey）
- P1-3/P2-5: 双属性签名 + signatureRequired 位于 textures JSON 内
- P1-4: 材质 URL base 用 site_url 优先
- P2-9: refresh 选角不属于该用户返回 403
- P3 杂项：超长 clientToken、search_profiles 元素类型等
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
    img = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def api(client, method, path, payload=None, **kwargs):
    fn = getattr(client, method)
    if payload is not None:
        kwargs.setdefault("content_type", "application/json")
        kwargs["data"] = json.dumps(payload)
    return fn(BASE + path, **kwargs)


@pytest.fixture()
def multi_profile_user(db):
    user = User.objects.create_user(
        email="m@x.com", nickname="m", password="password8", verified=True
    )
    Player.objects.create(uid=user, name="Alpha")
    Player.objects.create(uid=user, name="Beta")
    return user


def auth(client, username="m@x.com", **extra):
    payload = {"username": username, "password": "password8", **extra}
    resp = api(client, "post", "/authserver/authenticate", payload)
    assert resp.status_code == 200
    return resp.json()


def join(client, tokens, profile_id, server_id="srv-x"):
    resp = api(client, "post", "/sessionserver/session/minecraft/join", {
        "accessToken": tokens["accessToken"],
        "selectedProfile": profile_id,
        "serverId": server_id,
    })
    return resp


# ---- P0-1: refresh 选角后 join ----


class TestP0SelectProfileFlow:
    def test_refresh_select_then_join(self, client, multi_profile_user):
        alpha = Player.objects.get(name="Alpha")

        # 多角色：authenticate 不预绑定
        tokens = auth(client)
        assert "selectedProfile" not in tokens
        # 直接 join 未选角 → 403
        assert join(client, tokens, alpha.uuid).status_code == 403

        # refresh 选角 Alpha
        resp = api(client, "post", "/authserver/refresh", {
            "accessToken": tokens["accessToken"],
            "clientToken": tokens["clientToken"],
            "selectedProfile": {"id": alpha.uuid, "name": "Alpha"},
        })
        assert resp.status_code == 200
        refreshed = resp.json()
        assert refreshed["selectedProfile"]["id"] == alpha.uuid

        # join 用新令牌（refresh 后旧令牌已轮换）
        resp2 = join(client, refreshed, alpha.uuid)
        assert resp2.status_code == 204, resp2.content.decode()[:200]

        # DB 持久化验证（不止内存 JWT spr）
        from yggdrasil.models import YggdrasilToken

        row = YggdrasilToken.objects.get(access_token=refreshed["accessToken"])
        assert row.profile_id == alpha.uuid


# ---- P1-3/P2-5: 双属性签名 + signatureRequired 位置 ----


class TestDualPropertySignature:
    def _public_numbers(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding as _pad

        from options.models import Option

        pem = Option.objects.get_value("ygg_public_key")
        key = serialization.load_pem_public_key(pem.encode())
        return key, _pad.PKCS1v15(), hashes.SHA1()

    def test_every_property_signed(self, client, db):
        from accounts.models import User as U
        from yggdrasil.views import _ensure_signing_key

        _ensure_signing_key()
        user = U.objects.create_user(
            email="s@x.com", nickname="s", password="password8", verified=True
        )
        Player.objects.create(uid=user, name="Solo")
        profile = Player.objects.get(name="Solo")

        resp = client.get(
            BASE + f"/sessionserver/session/minecraft/profile/{profile.uuid}?unsigned=false"
        )
        assert resp.status_code == 200
        props = resp.json()["properties"]
        assert {p["name"] for p in props} == {"textures", "uploadableTextures"}

        key, pad, hash_alg = self._public_numbers()
        for prop in props:
            sig = __import__("base64").b64decode(prop["signature"])
            key.verify(sig, prop["value"].encode(), pad, hash_alg)  # 断言不过即失败

    def test_signature_required_inside_textures_json(self, client, db):
        import base64 as b64

        from accounts.models import User as U
        from yggdrasil.views import _ensure_signing_key

        _ensure_signing_key()
        user = U.objects.create_user(
            email="s2@x.com", nickname="s2", password="password8", verified=True
        )
        Player.objects.create(uid=user, name="Solo2")
        profile = Player.objects.get(name="Solo2")

        resp = client.get(
            BASE + f"/sessionserver/session/minecraft/profile/{profile.uuid}?unsigned=false"
        )
        prop = resp.json()["properties"][0]
        # signatureRequired 在 base64 解码后的 textures JSON 内（原版 L55）
        assert "signatureRequired" not in prop
        payload = json.loads(b64.b64decode(prop["value"]).decode())
        assert payload["signatureRequired"] is True

        # unsigned 时无该标记（原版仅 $unsigned===false 才标记）
        resp2 = client.get(
            BASE + f"/sessionserver/session/minecraft/profile/{profile.uuid}"
        )
        prop2 = resp2.json()["properties"][0]
        payload2 = json.loads(b64.b64decode(prop2["value"]).decode())
        assert "signatureRequired" not in payload2


# ---- P1-4: 材质 URL 用 site_url 优先（原版 forceRootUrl）----


class TestTextureUrlBase:
    def test_site_url_preferred_for_texture_url(self, client, db):
        from accounts.models import User as U
        from options.models import Option
        from textures.models import Texture
        from yggdrasil.views import _ensure_signing_key

        _ensure_signing_key()
        Option.objects.set("site_url", "https://skin.dreamweaver.top")
        user = U.objects.create_user(
            email="t@x.com", nickname="t", password="password8", verified=True
        )
        skin = Texture.objects.create(
            name="T", type="steve", hash="9" * 64, size=1, uploader=user
        )
        player = Player.objects.create(uid=user, name="TexUrl", tid_skin=skin)

        resp = client.get(
            BASE + f"/sessionserver/session/minecraft/profile/{player.uuid}"
        )
        value = json.loads(
            __import__("base64").b64decode(
                resp.json()["properties"][0]["value"]
            ).decode()
        )
        assert value["textures"]["SKIN"]["url"] == (
            "https://skin.dreamweaver.top/textures/" + "9" * 64
        )

    def test_fallback_to_request_host_without_site_url(self, client, db):
        from accounts.models import User as U
        from options.models import Option
        from textures.models import Texture
        from yggdrasil.views import _ensure_signing_key

        _ensure_signing_key()
        assert not Option.objects.get_value("site_url")
        user = U.objects.create_user(
            email="t2@x.com", nickname="t2", password="password8", verified=True
        )
        skin = Texture.objects.create(
            name="T2", type="steve", hash="8" * 64, size=1, uploader=user
        )
        player = Player.objects.create(uid=user, name="TexUrl2", tid_skin=skin)

        resp = client.get(
            BASE + f"/sessionserver/session/minecraft/profile/{player.uuid}"
        )
        value = json.loads(
            __import__("base64").b64decode(
                resp.json()["properties"][0]["value"]
            ).decode()
        )
        # 测试客户端默认 host=testserver，scheme=http
        assert value["textures"]["SKIN"]["url"] == (
            "http://testserver/textures/" + "8" * 64
        )


# ---- P2-9: refresh 选角不属于该用户 → 403（规范错误码表）----


class TestRefreshForeignProfile:
    def test_foreign_profile_rejected_403(self, client, multi_profile_user):
        tokens = auth(client)
        foreign = User.objects.create_user(
            email="f@x.com", nickname="f", password="password8", verified=True
        )
        foreigner = Player.objects.create(uid=foreign, name="Foreign")

        resp = api(client, "post", "/authserver/refresh", {
            "accessToken": tokens["accessToken"],
            "clientToken": tokens["clientToken"],
            "selectedProfile": {"id": foreigner.uuid, "name": "Foreign"},
        })
        assert resp.status_code == 403
        assert resp.json()["error"] == "ForbiddenOperationException"
