"""正版验证透传（hasJoined passthrough）测试：上游确认/绑定校验/改名同步/
纹理重签/缓存/全部失败路径 fail-closed。"""

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.files.storage import FileSystemStorage

from accounts.models import User
from oauth_bridge import httpclient
from oauth_bridge.models import MojangBinding
from options.models import Option

pytestmark = pytest.mark.django_db

HAS_JOINED = "/api/yggdrasil/sessionserver/session/minecraft/hasJoined"
MOJANG_URL = "https://sessionserver.mojang.com/session/minecraft/hasJoined"
TEXTURE_CDN = "http://textures.minecraft.net/texture/"

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)  # 头合法的最小占位


def enable(**opts):
    Option.objects.bulk_set({
        "microsoft_oauth_enabled": "1",
        "microsoft_bind_enabled": "1",
        "microsoft_passthrough_enabled": "1",
        "microsoft_client_id": "cid",
        "microsoft_client_secret": "sec",
    })
    for k, v in opts.items():
        Option.objects.set(k, str(v))


def sign_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def b64(obj):
    return base64.b64encode(json.dumps(obj).encode()).decode()


def mojang_profile(mc_uuid, mc_name, skin_hash=None, cape_hash=None):
    textures = {}
    if skin_hash:
        textures["SKIN"] = {"url": TEXTURE_CDN + skin_hash,
                            "metadata": {"model": "slim"}}
    if cape_hash:
        textures["CAPE"] = {"url": TEXTURE_CDN + cape_hash}
    return {
        "id": mc_uuid,
        "name": mc_name,
        "properties": [
            {"name": "textures",
             "value": b64({"timestamp": 1, "profileId": mc_uuid,
                           "profileName": mc_name, "textures": textures})},
        ],
    }


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    @property
    def content(self):
        return PNG


@pytest.fixture(autouse=True)
def _env(tmp_path, settings):
    settings.TEXTURE_STORAGE_ROOT = str(tmp_path / "t")
    from textures import storage as sm

    sm.texture_storage._storage = FileSystemStorage(location=settings.TEXTURE_STORAGE_ROOT)
    Option.objects.set("ygg_private_key", sign_key())
    yield
    sm.texture_storage._storage = None


@pytest.fixture()
def user():
    return User.objects.create_user(
        email="u@x.com", nickname="u", password="password8", verified=True
    )


@pytest.fixture()
def binding(user):
    return MojangBinding.objects.create(
        user=user, mc_uuid="a" * 32, mc_name="Notch"
    )


def patch_upstream(monkeypatch, *, profile=None, status=200, error=None, calls=None):
    def fake_get(url, *, params=None, timeout=None, headers=None):
        if calls is not None:
            calls.append({"url": url, "params": params, "timeout": timeout})
        if error is not None:
            raise error
        return FakeResp(profile if profile is not None else {}, status)

    monkeypatch.setattr(httpclient, "outbound_get", fake_get)


def has_joined(client, username="Notch", server_id="srv-1", ip=None):
    params = {"username": username, "serverId": server_id}
    if ip:
        params["ip"] = ip
    return client.get(HAS_JOINED, params)


# ---- 开关与拒绝路径 ----


class TestGatekeeping:
    def test_disabled_returns_204(self, client, user, binding, monkeypatch):
        enable(microsoft_passthrough_enabled="0")
        # 未启用时不应发起任何上游请求
        def fail_get(*a, **k):
            raise AssertionError("不应发起上游请求")

        monkeypatch.setattr(httpclient, "outbound_get", fail_get)
        resp = has_joined(client)
        assert resp.status_code == 204

    def test_upstream_204_rejected(self, client, user, binding, monkeypatch):
        enable()
        patch_upstream(monkeypatch, status=204)
        resp = has_joined(client)
        assert resp.status_code == 204

    def test_upstream_timeout_rejected(self, client, user, binding, monkeypatch):
        import requests

        enable()
        patch_upstream(monkeypatch, error=requests.RequestException("timeout"))
        resp = has_joined(client)
        assert resp.status_code == 204

    def test_upstream_exception_rejected(self, client, user, binding, monkeypatch):
        enable()
        patch_upstream(monkeypatch, error=RuntimeError("boom"))
        resp = has_joined(client)
        assert resp.status_code == 204

    def test_not_bound_rejected(self, client, user, monkeypatch):
        enable()
        # Mojang 确认会话存在，但该正版角色未绑定任何站内账号
        patch_upstream(
            monkeypatch, profile=mojang_profile("a" * 32, "Notch")
        )
        resp = has_joined(client)
        assert resp.status_code == 204

    def test_banned_user_rejected(self, client, user, binding, monkeypatch):
        enable()
        user.permission = User.Permission.BANNED
        user.save(update_fields=["permission"])
        patch_upstream(
            monkeypatch, profile=mojang_profile(binding.mc_uuid, "Notch")
        )
        resp = has_joined(client)
        assert resp.status_code == 204


# ---- 放行路径 ----


class TestApproved:
    def test_success_returns_genuine_uuid_and_name(self, client, user, binding,
                                                   monkeypatch):
        enable()
        patch_upstream(
            monkeypatch, profile=mojang_profile(binding.mc_uuid, "Notch")
        )
        resp = has_joined(client)
        assert resp.status_code == 200
        data = resp.json()
        # 透传以正版 UUID 为准（与正版服务器数据互通）
        assert data["id"] == "a" * 32
        assert data["name"] == "Notch"
        prop = data["properties"][0]
        assert prop["name"] == "textures"
        assert prop["signature"]  # 皮肤站私钥签名必带

    def test_ip_forwarded_to_upstream(self, client, user, binding, monkeypatch):
        enable()
        calls = []
        patch_upstream(
            monkeypatch, profile=mojang_profile(binding.mc_uuid, "Notch"),
            calls=calls,
        )
        resp = has_joined(client, ip="1.2.3.4")
        assert resp.status_code == 200
        assert calls[0]["url"] == MOJANG_URL
        assert calls[0]["params"]["username"] == "Notch"
        assert calls[0]["params"]["serverId"] == "srv-1"
        assert calls[0]["params"]["ip"] == "1.2.3.4"
        assert calls[0]["timeout"] <= 5

    def test_rename_synced(self, client, user, binding, monkeypatch):
        enable()
        patch_upstream(
            monkeypatch, profile=mojang_profile(binding.mc_uuid, "NewName")
        )
        resp = has_joined(client, username="NewName")
        assert resp.status_code == 200
        binding.refresh_from_db()
        assert binding.mc_name == "NewName"
        assert resp.json()["name"] == "NewName"

    def test_texture_downloaded_and_resigned(self, client, user, binding,
                                              monkeypatch):
        enable()
        patch_upstream(
            monkeypatch,
            profile=mojang_profile(binding.mc_uuid, "Notch", skin_hash="ab" * 32),
        )
        resp = has_joined(client)
        assert resp.status_code == 200
        binding.refresh_from_db()
        expected_local = hashlib.sha256(PNG).hexdigest()
        assert binding.skin_local_hash == expected_local
        assert binding.skin_model == "slim"
        # 签名纹理 URL 指向本站本地文件
        import urllib.parse

        prop = resp.json()["properties"][0]
        payload = json.loads(base64.b64decode(prop["value"]))
        url = payload["textures"]["SKIN"]["url"]
        path = urllib.parse.urlsplit(url).path
        assert path == f"/textures/{expected_local}"
        assert payload["textures"]["SKIN"]["metadata"]["model"] == "slim"
        # 本地存储确有该文件
        from textures.storage import texture_storage

        assert texture_storage.exists(expected_local)

    def test_second_request_hits_cache(self, client, user, binding, monkeypatch):
        enable()
        calls = []
        patch_upstream(
            monkeypatch, profile=mojang_profile(binding.mc_uuid, "Notch"),
            calls=calls,
        )
        assert has_joined(client).status_code == 200
        assert has_joined(client).status_code == 200
        assert len(calls) == 1  # 第二次走透传缓存

    def test_cache_bypassed_on_ip_mismatch(self, client, user, binding, monkeypatch):
        enable()
        calls = []
        patch_upstream(
            monkeypatch, profile=mojang_profile(binding.mc_uuid, "Notch"),
            calls=calls,
        )
        assert has_joined(client, ip="1.1.1.1").status_code == 200
        assert has_joined(client, ip="2.2.2.2").status_code == 200
        assert len(calls) == 2  # IP 不一致必须重新问上游

    def test_invalid_signing_key_rejected(self, client, user, binding, monkeypatch):
        from options.models import Option as Opt

        # 无效 PEM（非空 → 不会触发惰性重生成，load 失败须 fail-closed）
        Opt.objects.set("ygg_private_key", "not-a-valid-pem")
        enable()
        patch_upstream(
            monkeypatch, profile=mojang_profile(binding.mc_uuid, "Notch")
        )
        resp = has_joined(client)
        assert resp.status_code == 204


# ---- 与本地会话互不干扰 ----


class TestLocalSessionIntact:
    def test_local_join_flow_still_works(self, client, db, monkeypatch):
        """未开启透传时，本地 join → hasJoined 流程不受影响。"""
        enable(microsoft_passthrough_enabled="0")
        from oauth_bridge import httpclient

        monkeypatch.setattr(httpclient, "outbound_get", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("不应发起上游请求")))
        client.get(HAS_JOINED, {"username": "X", "serverId": "S"})
        # 204 即可（本地无会话）
        resp = client.get(HAS_JOINED, {"username": "X", "serverId": "S"})
        assert resp.status_code == 204
