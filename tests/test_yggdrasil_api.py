"""Yggdrasil API 协议测试（TDD 先行）。

对照 yggdrasil-api 插件协议端点（authlib-injector 规范）：
- POST /api/yggdrasil/authserver/authenticate
- POST /api/yggdrasil/authserver/refresh / validate / invalidate / signout
- POST /api/yggdrasil/sessionserver/session/minecraft/join
- GET  /api/yggdrasil/sessionserver/session/minecraft/hasJoined
- GET  /api/yggdrasil/sessionserver/session/minecraft/profile/{uuid}
- POST /api/yggdrasil/api/profiles/minecraft
- GET  /api/yggdrasil/api/users/profiles/minecraft/{username}
- PUT/DELETE /api/yggdrasil/api/user/profile/{uuid}/{type}

安全修复（静态审查成果）：
- Throttle 键改为 IP（原版 attacker-controlled username）
- 请求体大小上限（防内存耗尽）
"""

import io
import json

import pytest
from PIL import Image

from accounts.models import User
from players.models import Player

pytestmark = pytest.mark.django_db

BASE = "/api/yggdrasil"


def api(client, method, path, payload=None, **headers):
    fn = getattr(client, method)
    kwargs = {"content_type": "application/json", **headers}
    body = json.dumps(payload) if payload is not None else None
    if body is not None:
        kwargs["data"] = body
    return fn(BASE + path, **kwargs)


@pytest.fixture()
def seeded(db):
    user = User.objects.create_user(
        email="y@x.com",
        nickname="y",
        password="password8",
        verified=True,
    )
    player = Player.objects.create(uid=user, name="Steve")
    return user, player


# ---- 根端点（authlib-injector 发现）----


class TestHello:
    def test_hello_meta(self, client, db):
        resp = client.get(BASE + "/")
        assert resp.status_code == 200
        data = resp.json()
        assert "serverName" in data["meta"]
        assert isinstance(data["skinDomains"], list)
        assert "signaturePublickey" in data


# ---- authenticate ----


class TestAuthenticate:
    def test_success_by_email(self, client, seeded):
        resp = api(client, "post", "/authserver/authenticate", {
            "agent": {"name": "Minecraft", "version": 1},
            "username": "y@x.com",
            "password": "password8",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "accessToken" in data
        assert "clientToken" in data
        assert data["availableProfiles"][0]["name"] == "Steve"
        # 单角色自动选中
        assert data["selectedProfile"]["name"] == "Steve"

    def test_success_by_player_name(self, client, seeded):
        resp = api(client, "post", "/authserver/authenticate", {
            "username": "Steve",
            "password": "password8",
        })
        assert resp.status_code == 200
        assert resp.json()["availableProfiles"][0]["name"] == "Steve"

    def test_wrong_password_403(self, client, seeded):
        resp = api(client, "post", "/authserver/authenticate", {
            "username": "y@x.com", "password": "wrong-pass",
        })
        assert resp.status_code == 403
        assert resp.json()["error"] == "ForbiddenOperationException"

    def test_rate_limited_by_ip(self, client, seeded):
        """防爆破：IP 维度限速（原版 username 键缺陷修复）。"""
        from django.core.cache import cache
        from django.test import override_settings

        with override_settings(YGG_RATE_LIMIT_SECONDS=1.0):
            cache.clear()
            api(client, "post", "/authserver/authenticate", {
                "username": "y@x.com", "password": "wrong-1",
            })
            resp = api(client, "post", "/authserver/authenticate", {
                "username": "y@x.com", "password": "wrong-2",
            })
            assert resp.status_code == 429

    def test_request_body_size_limit(self, client, seeded):
        """请求体大小上限（安全修复：防内存耗尽 DoS）。"""
        from django.test import override_settings

        with override_settings(YGG_MAX_BODY_BYTES=1024):
            huge = {"username": "y@x.com", "password": "x" * 4096}
            resp = api(client, "post", "/authserver/authenticate", huge)
            assert resp.status_code in (413, 400)

    def test_content_type_required(self, client, seeded):
        resp = client.post(
            BASE + "/authserver/authenticate",
            data="username=y@x.com",
            content_type="application/x-www-form-urlencoded",
        )
        assert resp.status_code in (400, 415)


# ---- 令牌生命周期 ----


def authenticate(client, email="y@x.com", password="password8"):
    resp = api(client, "post", "/authserver/authenticate", {
        "username": email, "password": password, "clientToken": "fixed-client",
    })
    detail = f"{resp.status_code} {resp.content.decode()[:200]}"
    assert resp.status_code == 200, f"authenticate failed: {detail}"
    return resp.json()


class TestTokenLifecycle:
    def test_validate(self, client, seeded):
        tokens = authenticate(client)
        resp = api(client, "post", "/authserver/validate", {
            "accessToken": tokens["accessToken"], "clientToken": "fixed-client",
        })
        assert resp.status_code == 204

    def test_validate_wrong_client_token(self, client, seeded):
        tokens = authenticate(client)
        resp = api(client, "post", "/authserver/validate", {
            "accessToken": tokens["accessToken"], "clientToken": "other",
        })
        assert resp.status_code == 403

    def test_validate_garbage_403(self, client, seeded):
        resp = api(client, "post", "/authserver/validate", {"accessToken": "junk"})
        assert resp.status_code == 403

    def test_refresh_rotates_token(self, client, seeded):
        tokens = authenticate(client)
        resp = api(client, "post", "/authserver/refresh", {
            "accessToken": tokens["accessToken"], "clientToken": "fixed-client",
        })
        assert resp.status_code == 200
        new_access = resp.json()["accessToken"]
        assert new_access != tokens["accessToken"]
        # 旧 token 失效
        old_check = api(client, "post", "/authserver/validate", {
            "accessToken": tokens["accessToken"],
        })
        assert old_check.status_code == 403
        # 新 token 有效
        new_check = api(client, "post", "/authserver/validate", {
            "accessToken": new_access,
        })
        assert new_check.status_code == 204

    def test_invalidate(self, client, seeded):
        tokens = authenticate(client)
        resp = api(client, "post", "/authserver/invalidate", {
            "accessToken": tokens["accessToken"],
        })
        assert resp.status_code == 204
        check = api(client, "post", "/authserver/validate", {
            "accessToken": tokens["accessToken"],
        })
        assert check.status_code == 403

    def test_signout_revokes_all(self, client, seeded):
        tokens = authenticate(client)
        tokens2 = authenticate(client)
        resp = api(client, "post", "/authserver/signout", {
            "username": "y@x.com", "password": "password8",
        })
        assert resp.status_code == 204
        for t in (tokens, tokens2):
            check = api(client, "post", "/authserver/validate", {
                "accessToken": t["accessToken"],
            })
            assert check.status_code == 403


# ---- 会话（join/hasJoined）----


class TestSessionServer:
    def test_join_and_has_joined(self, client, seeded):
        tokens = authenticate(client)
        uuid = Player.objects.get(name="Steve").uuid

        join = api(client, "post", "/sessionserver/session/minecraft/join", {
            "accessToken": tokens["accessToken"],
            "selectedProfile": uuid,
            "serverId": "server-42",
        })
        assert join.status_code == 204

        has = client.get(BASE + "/sessionserver/session/minecraft/hasJoined", {
            "username": "Steve", "serverId": "server-42",
        })
        assert has.status_code == 200
        data = has.json()
        assert data["id"] == uuid
        props = {p["name"]: p for p in data["properties"]}
        assert "textures" in props

    def test_has_joined_wrong_server_204(self, client, seeded):
        tokens = authenticate(client)
        uuid = tokens["selectedProfile"]["id"]
        api(client, "post", "/sessionserver/session/minecraft/join", {
            "accessToken": tokens["accessToken"],
            "selectedProfile": uuid,
            "serverId": "srv",
        })
        resp = client.get(BASE + "/sessionserver/session/minecraft/hasJoined", {
            "username": "Steve", "serverId": "other-server",
        })
        assert resp.status_code == 204

    def test_join_invalid_token_403(self, client, seeded):
        resp = api(client, "post", "/sessionserver/session/minecraft/join", {
            "accessToken": "bogus", "selectedProfile": "0" * 32, "serverId": "s",
        })
        assert resp.status_code == 403

    def test_recent_join_answered(self, client, seeded):
        tokens = authenticate(client)
        uuid = tokens["selectedProfile"]["id"]
        api(client, "post", "/sessionserver/session/minecraft/join", {
            "accessToken": tokens["accessToken"], "selectedProfile": uuid,
            "serverId": "srv-1",
        })
        url = BASE + "/sessionserver/session/minecraft/hasJoined"
        resp = client.get(url, {"username": "Steve", "serverId": "srv-1"})
        assert resp.status_code == 200


# ---- Profile 查询 ----


class TestProfileQueries:
    def test_get_profile_by_uuid(self, client, seeded):
        _, player = seeded
        resp = client.get(
            BASE + f"/sessionserver/session/minecraft/profile/{player.uuid}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Steve"
        assert data["id"] == player.uuid

    def test_profile_204_for_unknown(self, client, seeded):
        resp = client.get(BASE + "/sessionserver/session/minecraft/profile/" + "f" * 32)
        assert resp.status_code == 204

    def test_search_profiles_bulk(self, client, seeded):
        resp = api(client, "post", "/api/profiles/minecraft", ["Steve"])
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["name"] == "Steve"

    def test_search_profiles_max_limit(self, client, seeded):
        from options.models import Option

        Option.objects.set("ygg_search_profile_max", "5")
        resp = api(
            client, "post", "/api/profiles/minecraft", [f"P{i}" for i in range(10)]
        )
        assert resp.status_code == 403

    def test_search_single_profile(self, client, seeded):
        resp = client.get(BASE + "/api/users/profiles/minecraft/Steve")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Steve"

    def test_search_single_unknown_204(self, client, seeded):
        resp = client.get(BASE + "/api/users/profiles/minecraft/GhostName")
        assert resp.status_code == 204


# ---- 材质上传（Bearer 认证）----


class TestTextureUpload:
    def _put_skin(self, client, api_token, uuid):
        img = Image.new("RGBA", (64, 32), (10, 20, 30, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return client.put(
            f"{BASE}/api/user/profile/{uuid}/skin",
            data=buf.getvalue(),
            content_type="image/png",
            headers={"Authorization": f"Bearer {api_token}"},
        )

    def test_upload_requires_bearer(self, client, seeded):
        _, player = seeded
        resp = client.put(f"{BASE}/api/user/profile/{player.uuid}/skin", data=b"")
        assert resp.status_code in (401, 403)

    def test_upload_and_reset_skin(self, client, seeded, tmp_path, settings):
        from django.core.files.storage import FileSystemStorage

        settings.TEXTURE_STORAGE_ROOT = str(tmp_path / "textures")
        from textures import storage as storage_mod

        storage_mod.texture_storage._storage = FileSystemStorage(
            location=settings.TEXTURE_STORAGE_ROOT
        )

        tokens = authenticate(client)
        token = tokens["accessToken"]
        uuid = tokens["selectedProfile"]["id"]

        resp = self._put_skin(client, token, uuid)
        assert resp.status_code == 204, resp.content.decode()[:200]

        player = Player.objects.get(name="Steve")
        assert player.tid_skin is not None

        reset = client.delete(
            f"{BASE}/api/user/profile/{uuid}/skin",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert reset.status_code == 204
        player.refresh_from_db()
        assert player.tid_skin is None
