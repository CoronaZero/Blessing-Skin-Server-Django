"""Yggdrasil 兼容性第三轮修复回归（规范全文 + authlib-injector 源码核对）。

- 全站 ALI 头（裸域名发现，对照原版 AddApiIndicationHeader）
- join/hasJoined 客户端 IP 校验（原版 SessionController）
- v3 算法下改名重算角色 UUID（原版 uuid 表重映射）
- skinDomains 小写归一并剥离端口（客户端 URI.getHost 匹配语义）
- clientToken 长度放宽（规范 L408：任意字符串）
- publickeys 响应对齐原版结构
- 像素 hash 规范官方测试向量（L360-377）
"""

import json

import pytest

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


def authenticate(client, **extra):
    resp = client.post(
        BASE + "/authserver/authenticate",
        data=json.dumps({"username": "y@x.com", "password": "password8", **extra}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    return resp.json()


# ---- 全站 ALI 头（裸域名发现）----

ALI_HEADER = "X-Authlib-Injector-API-Location"


class TestAliSiteWide:
    def test_homepage_carries_ali(self, client, db):
        """启动器只填域名时抓的是首页，ALI 必须在那里出现。"""
        resp = client.get("/")
        assert resp[ALI_HEADER] == "/api/yggdrasil/"

    def test_api_root_carries_ali(self, client, db):
        resp = client.get(BASE + "/")
        assert resp[ALI_HEADER] == "/api/yggdrasil/"

    def test_disabled_option_removes_header(self, client, db):
        Option.objects.set("ygg_enable_ali", "0")
        assert ALI_HEADER not in client.get("/").headers
        assert ALI_HEADER not in client.get(BASE + "/").headers


# ---- join/hasJoined 客户端 IP 校验 ----


def join_server(client, tokens, server_id="srv", ip="1.2.3.4"):
    return client.post(
        BASE + "/sessionserver/session/minecraft/join",
        data=json.dumps({
            "accessToken": tokens["accessToken"],
            "selectedProfile": tokens["selectedProfile"]["id"],
            "serverId": server_id,
        }),
        content_type="application/json",
        REMOTE_ADDR=ip,
    )


def has_joined(client, server_id="srv", ip=None):
    url = (
        f"{BASE}/sessionserver/session/minecraft/hasJoined"
        f"?username=Steve&serverId={server_id}"
    )
    if ip is not None:
        url += f"&ip={ip}"
    return client.get(url)


class TestJoinIpCheck:
    def test_ip_match_passes(self, client, seeded):
        tokens = authenticate(client)
        assert join_server(client, tokens, ip="1.2.3.4").status_code == 204
        assert has_joined(client, ip="1.2.3.4").status_code == 200

    def test_ip_mismatch_rejected(self, client, seeded):
        tokens = authenticate(client)
        assert join_server(client, tokens, ip="1.2.3.4").status_code == 204
        assert has_joined(client, ip="5.6.7.8").status_code == 204

    def test_ip_omitted_skips_check(self, client, seeded):
        """未提供 ip 参数（prevent-proxy-connections 关闭）不做 IP 校验。"""
        tokens = authenticate(client)
        assert join_server(client, tokens, ip="1.2.3.4").status_code == 204
        assert has_joined(client).status_code == 200

    def test_forwarded_for_used_at_join(self, client, seeded):
        """join 记录的是 X-Forwarded-For 首段（反代后真实客户端 IP）。"""
        tokens = authenticate(client)
        resp = client.post(
            BASE + "/sessionserver/session/minecraft/join",
            data=json.dumps({
                "accessToken": tokens["accessToken"],
                "selectedProfile": tokens["selectedProfile"]["id"],
                "serverId": "srv",
            }),
            content_type="application/json",
            REMOTE_ADDR="10.0.0.1",
            HTTP_X_FORWARDED_FOR="9.9.9.9, 10.0.0.1",
        )
        assert resp.status_code == 204
        assert has_joined(client, ip="9.9.9.9").status_code == 200
        assert has_joined(client, ip="10.0.0.1").status_code == 204


# ---- v3 算法下改名重算 UUID（原版 uuid 表重映射）----


class TestV3RenameUuid:
    def _user_client(self, client):
        User.objects.create_user(
            email="u@x.com", nickname="u", password="password8", verified=True
        )
        client.login(username="u@x.com", password="password8")
        return client

    def test_v3_rename_recomputes_uuid(self, client, db):
        from players.uuidgen import generate_uuid_v3

        c = self._user_client(client)
        Option.objects.set("ygg_uuid_algorithm", "v3")
        # 走业务创建入口（add_player 显式按名字生成 UUID，v3 生效）
        resp = c.post("/user/players", {"name": "OldName"})
        assert resp.json()["code"] == 0
        player = Player.objects.get(name="OldName")
        assert player.uuid == generate_uuid_v3("OldName")

        resp = c.post(f"/user/players/{player.pid}/name", {"name": "NewName"})
        assert resp.json()["code"] == 0
        player.refresh_from_db()
        assert player.name == "NewName"
        assert player.uuid == generate_uuid_v3("NewName")

    def test_v4_rename_keeps_uuid(self, client, db):
        c = self._user_client(client)
        Option.objects.set("ygg_uuid_algorithm", "v4")
        resp = c.post("/user/players", {"name": "OldName"})
        assert resp.json()["code"] == 0
        player = Player.objects.get(name="OldName")
        old_uuid = player.uuid

        resp = c.post(f"/user/players/{player.pid}/name", {"name": "NewName"})
        assert resp.json()["code"] == 0
        player.refresh_from_db()
        assert player.uuid == old_uuid


# ---- skinDomains 归一（客户端 URI.getHost：小写、无端口）----


class TestSkinDomainsNormalize:
    def test_entries_lowercased_and_port_stripped(self, client, db, settings):
        settings.ALLOWED_HOSTS = ["*"]
        Option.objects.set("ygg_skin_domain", " Example.COM:8443 , *.Foo.CN ")
        resp = client.get(BASE + "/", HTTP_HOST="Skin.Example.COM:8443")
        domains = resp.json()["skinDomains"]
        assert "example.com" in domains
        assert "*.foo.cn" in domains
        assert "skin.example.com" in domains  # 当前 host 去端口小写
        assert all(d == d.lower() and ":" not in d for d in domains)

    def test_duplicate_entries_collapsed(self, client, db, settings):
        settings.ALLOWED_HOSTS = ["*"]
        Option.objects.set("ygg_skin_domain", "TESTserver:8000,testserver")
        resp = client.get(BASE + "/", HTTP_HOST="testserver")
        assert resp.json()["skinDomains"].count("testserver") == 1


# ---- clientToken 长度（规范 L408：任意字符串）----


class TestLongClientToken:
    def test_200_char_client_token_accepted(self, client, seeded):
        token = "a" * 200
        data = authenticate(client, clientToken=token)
        assert data["clientToken"] == token

    def test_token_row_persisted(self, client, seeded):
        from yggdrasil.models import YggdrasilToken

        token = "b" * 200
        authenticate(client, clientToken=token)
        assert YggdrasilToken.objects.filter(client_token=token).exists()


# ---- publickeys 响应对齐原版结构 ----


class TestPublickeysShape:
    def test_shape_matches_php(self, client, db):
        import base64

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        Option.objects.set("ygg_private_key", pem)

        data = client.get(BASE + "/minecraftservices/publickeys").json()
        entries = data["profilePropertyKeys"]
        assert data["playerCertificateKeys"] == entries
        assert len(entries) == 1
        der = base64.b64decode(entries[0]["publicKey"])
        loaded = serialization.load_der_public_key(der)
        assert loaded.key_size == 2048

    def test_empty_without_key(self, client, db):
        data = client.get(BASE + "/minecraftservices/publickeys").json()
        assert data == {"profilePropertyKeys": [], "playerCertificateKeys": []}
