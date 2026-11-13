"""Yggdrasil P0/P1 协议修复回归（定向）。"""

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as _padding

from accounts.models import User
from options.models import Option
from players.models import Player

pytestmark = pytest.mark.django_db

BASE = "/api/yggdrasil"


@pytest.fixture()
def seeded(db):
    user = User.objects.create_user(
        email="y@x.com", nickname="y", password="password8", verified=True
    )
    Player.objects.create(uid=user, name="Steve")
    return user


def authenticate(client):
    resp = client.post(
        BASE + "/authserver/authenticate",
        data=json.dumps({"username": "y@x.com", "password": "password8"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    return resp.json()


class TestP0Fixes:
    def test_signature_is_sha1withrsa(self, client, seeded):
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        Option.objects.set("ygg_private_key", key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode())
        tokens = authenticate(client)
        uuid = tokens["selectedProfile"]["id"]
        resp = client.get(BASE + f"/sessionserver/session/minecraft/profile/{uuid}?unsigned=false")
        props = {p["name"]: p for p in resp.json()["properties"]}
        sig = base64.b64decode(props["textures"]["signature"])
        value = props["textures"]["value"].encode()
        # SHA1 验签通过（若 SHA256 则此处抛异常）
        key.public_key().verify(sig, value, _padding.PKCS1v15(), hashes.SHA1())

    def test_uploadable_textures_property(self, client, seeded):
        tokens = authenticate(client)
        uuid = tokens["selectedProfile"]["id"]
        resp = client.get(BASE + f"/sessionserver/session/minecraft/profile/{uuid}")
        names = [p["name"] for p in resp.json()["properties"]]
        assert "uploadableTextures" in names

    def test_bearer_invalid_returns_401(self, client, seeded, db):
        import io as _io

        from PIL import Image
        buf = _io.BytesIO()
        Image.new("RGBA", (64, 32), (1, 2, 3, 255)).save(buf, format="PNG")
        resp = client.put(
            f"{BASE}/api/user/profile/{'0'*32}/skin",
            data=buf.getvalue(), content_type="image/png",
            headers={"Authorization": "Bearer junk"},
        )
        assert resp.status_code == 401

    def test_multipart_upload(self, client, seeded, tmp_path, settings):
        import io as _io

        from django.core.files.storage import FileSystemStorage
        from PIL import Image

        settings.TEXTURE_STORAGE_ROOT = str(tmp_path / "t")
        from textures import storage as sm

        sm.texture_storage._storage = FileSystemStorage(location=settings.TEXTURE_STORAGE_ROOT)
        tokens = authenticate(client)
        uuid = tokens["selectedProfile"]["id"]
        img = Image.new("RGBA", (64, 64), (9, 8, 7, 255))
        buf2 = _io.BytesIO()
        img.save(buf2, format="PNG")
        png_bytes = buf2.getvalue()

        # 手工构造 multipart（Django 测试客户端 put 不支持 multipart 编码）
        boundary = "BSSBoundary7384"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="model"\r\n\r\nslim\r\n'
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="skin.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode() + png_bytes + f"\r\n--{boundary}--\r\n".encode()

        resp = client.put(
            f"{BASE}/api/user/profile/{uuid}/skin",
            data=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            headers={"Authorization": f"Bearer {tokens['accessToken']}"},
        )
        assert resp.status_code == 204, resp.content.decode()[:300]
        player = Player.objects.get(name="Steve")
        assert player.tid_skin.type == "alex"  # multipart model 生效


class TestP1Fixes:
    def test_refresh_resets_created_at(self, client, seeded):
        from yggdrasil.models import YggdrasilToken

        tokens = authenticate(client)
        tokens = authenticate(client)  # 同 clientToken 二连发
        refresh = client.post(
            BASE + "/authserver/refresh",
            data=json.dumps({
                "accessToken": tokens["accessToken"],
                "clientToken": tokens["clientToken"],
            }),
            content_type="application/json",
        )
        assert refresh.status_code == 200
        row = YggdrasilToken.objects.get(access_token=refresh.json()["accessToken"])
        assert row.is_refreshable()

    def test_refresh_cannot_rebind_assigned_profile(self, client, seeded):
        tokens = authenticate(client)
        Player.objects.create(uid=User.objects.get(email="y@x.com"), name="Other")
        resp = client.post(
            BASE + "/authserver/refresh",
            data=json.dumps({
                "accessToken": tokens["accessToken"],
                "clientToken": tokens["clientToken"],
                "selectedProfile": {"id": Player.objects.get(name="Other").uuid},
            }),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_token_limit_enforced(self, client, seeded):
        from options.models import Option as O

        O.objects.set("ygg_tokens_limit", "2")
        for _ in range(4):
            authenticate(client)
        from yggdrasil.models import YggdrasilToken

        assert YggdrasilToken.objects.count() == 2

    def test_multi_profile_no_auto_bind(self, client, seeded):
        Player.objects.create(uid=User.objects.get(email="y@x.com"), name="Second")
        tokens = authenticate(client)
        assert "selectedProfile" not in tokens

    def test_join_empty_bind_rejected(self, client, seeded):
        Player.objects.create(uid=User.objects.get(email="y@x.com"), name="Second")
        tokens = authenticate(client)  # 多角色 → 未绑定
        resp = client.post(
            BASE + "/sessionserver/session/minecraft/join",
            data=json.dumps({
                "accessToken": tokens["accessToken"],
                "selectedProfile": Player.objects.get(name="Steve").uuid,
                "serverId": "srv",
            }),
            content_type="application/json",
        )
        assert resp.status_code == 403
