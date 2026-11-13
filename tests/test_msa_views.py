"""oauth_bridge 视图流程测试：登录/注册/绑定正版角色/解绑 全分支。

外部 HTTP（token 端点、Xbox 链路、纹理下载）全部 monkeypatch，
不打真实网络。
"""

import base64
import json

import pytest
from django.core.files.storage import FileSystemStorage

from accounts.models import User
from oauth_bridge import msauth
from oauth_bridge.models import MojangBinding, MsaIdentity
from options.models import Option

pytestmark = pytest.mark.django_db

CALLBACK = "/oauth/microsoft/callback"


def enable(**opts):
    Option.objects.bulk_set({
        "microsoft_oauth_enabled": "1",
        "microsoft_login_enabled": "1",
        "microsoft_register_enabled": "1",
        "microsoft_bind_enabled": "1",
        "microsoft_client_id": "cid",
        "microsoft_client_secret": "sec",
    })
    for k, v in opts.items():
        Option.objects.set(k, str(v))


def b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def fake_login_identity(sub="sub-1", email="ms@live.com", name="MS User"):
    """构造 fetch_login_identity 的替身。"""
    def _fake(client_id, client_secret, code, redirect_uri):
        return {"sub": sub, "email": email, "name": name}
    return _fake


def make_mojang_profile(mc_uuid="a" * 32, mc_name="Notch", skin_url=None):
    textures = {}
    if skin_url:
        textures["SKIN"] = {"url": skin_url}
    return {
        "id": mc_uuid,
        "name": mc_name,
        "properties": [
            {"name": "textures",
             "value": b64({"timestamp": 1, "profileId": mc_uuid,
                           "profileName": mc_name, "textures": textures})},
        ],
    }


@pytest.fixture(autouse=True)
def _tmp_storage(tmp_path, settings):
    settings.TEXTURE_STORAGE_ROOT = str(tmp_path / "t")
    from textures import storage as sm

    sm.texture_storage._storage = FileSystemStorage(location=settings.TEXTURE_STORAGE_ROOT)
    yield
    sm.texture_storage._storage = None


@pytest.fixture()
def user():
    return User.objects.create_user(
        email="u@x.com", nickname="u", password="password8", verified=True
    )


def login(client, user):
    client.force_login(user)


# ---- 发起端点 ----


class TestLoginStart:
    def test_disabled_shows_error_page(self, client):
        resp = client.get("/oauth/microsoft/login")
        assert resp.status_code == 200
        assert "Microsoft 登录未启用".encode() in resp.content

    def test_enabled_redirects_to_microsoft(self, client):
        enable()
        resp = client.get("/oauth/microsoft/login")
        assert resp.status_code == 302
        assert resp["Location"].startswith(
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
        )
        assert "scope=openid+email+profile" in resp["Location"]
        assert "state=" in resp["Location"]

    def test_logged_in_user_becomes_bind_intent(self, client, user):
        enable()
        login(client, user)
        resp = client.get("/oauth/microsoft/login")
        assert resp.status_code == 302
        assert "scope=openid+email+profile" in resp["Location"]

    def test_logged_in_already_bound_rejected(self, client, user):
        enable()
        MsaIdentity.objects.create(user=user, sub="sub-x")
        login(client, user)
        resp = client.get("/oauth/microsoft/login")
        assert resp.status_code == 200
        assert "已绑定".encode() in resp.content


class TestGenuineStart:
    def test_requires_login(self, client):
        enable()
        resp = client.get("/oauth/microsoft/genuine")
        assert resp.status_code == 302
        assert "/auth/login" in resp["Location"]

    def test_disabled_shows_error_page(self, client, user):
        login(client, user)
        resp = client.get("/oauth/microsoft/genuine")
        assert resp.status_code == 200
        assert "正版角色绑定未启用".encode() in resp.content

    def test_enabled_redirects_with_xbox_scope(self, client, user):
        enable()
        login(client, user)
        resp = client.get("/oauth/microsoft/genuine")
        assert resp.status_code == 302
        assert "scope=XboxLive.signin" in resp["Location"]

    def test_already_bound_rejected(self, client, user):
        enable()
        MojangBinding.objects.create(user=user, mc_uuid="b" * 32, mc_name="Jeb")
        login(client, user)
        resp = client.get("/oauth/microsoft/genuine")
        assert "已绑定".encode() in resp.content

    def test_cooldown_blocks(self, client, user):
        from oauth_bridge.models import UnbindCooldown

        enable(microsoft_unbind_cooldown_days=7)
        UnbindCooldown.record(user, "genuine")
        login(client, user)
        resp = client.get("/oauth/microsoft/genuine")
        assert "冷却".encode() in resp.content


# ---- 回调：登录意图 ----


class TestLoginCallback:
    def _start(self, client):
        enable()
        resp = client.get("/oauth/microsoft/login")
        from urllib.parse import parse_qs, urlsplit

        return parse_qs(urlsplit(resp["Location"]).query)["state"][0]

    def test_bound_user_logs_in(self, client, user, monkeypatch):
        MsaIdentity.objects.create(user=user, sub="sub-1")
        st = self._start(client)
        monkeypatch.setattr(msauth, "fetch_login_identity", fake_login_identity())
        resp = client.get(CALLBACK, {"code": "c", "state": st})
        assert resp.status_code == 302
        assert resp["Location"] == "/user"
        assert str(user.uid) == client.session["_auth_user_id"]

    def test_banned_user_rejected(self, client, user, monkeypatch):
        user.permission = User.Permission.BANNED
        user.save(update_fields=["permission"])
        MsaIdentity.objects.create(user=user, sub="sub-1")
        st = self._start(client)
        monkeypatch.setattr(msauth, "fetch_login_identity", fake_login_identity())
        resp = client.get(CALLBACK, {"code": "c", "state": st})
        assert resp.status_code == 200
        assert "封禁".encode() in resp.content
        assert "_auth_user_id" not in client.session

    def test_unbound_redirects_to_choose(self, client, monkeypatch):
        enable()
        st = self._start(client)
        monkeypatch.setattr(msauth, "fetch_login_identity", fake_login_identity())
        resp = client.get(CALLBACK, {"code": "c", "state": st})
        assert resp.status_code == 302
        assert resp["Location"] == "/oauth/microsoft/choose"
        assert client.session["msa_pending"]["sub"] == "sub-1"

    def test_invalid_state_rejected(self, client, monkeypatch):
        enable()
        monkeypatch.setattr(msauth, "fetch_login_identity", fake_login_identity())
        resp = client.get(CALLBACK, {"code": "c", "state": "bogus"})
        assert "已过期或无效".encode() in resp.content

    def test_microsoft_error_param_shows_error(self, client):
        enable()
        resp = client.get(CALLBACK, {"error": "access_denied", "state": "x"})
        assert "Microsoft 授权被取消".encode() in resp.content

    def test_bind_intent_conflict_sub(self, client, user, monkeypatch):
        """绑定意图下，sub 已属于其他账号 → 拒绝（防账号接管核心）。"""
        other = User.objects.create_user(
            email="o@x.com", nickname="o", password="password8"
        )
        MsaIdentity.objects.create(user=other, sub="sub-1")
        enable()
        login(client, user)
        resp = client.get("/oauth/microsoft/login")
        from urllib.parse import parse_qs, urlsplit

        st = parse_qs(urlsplit(resp["Location"]).query)["state"][0]
        monkeypatch.setattr(msauth, "fetch_login_identity", fake_login_identity())
        resp = client.get(CALLBACK, {"code": "c", "state": st})
        assert "已绑定其他".encode() in resp.content
        user.refresh_from_db()
        assert not hasattr(user, "msa_identity") or user.msa_identity is None

    def test_bind_intent_success(self, client, user, monkeypatch):
        enable()
        login(client, user)
        resp = client.get("/oauth/microsoft/login")
        from urllib.parse import parse_qs, urlsplit

        st = parse_qs(urlsplit(resp["Location"]).query)["state"][0]
        monkeypatch.setattr(msauth, "fetch_login_identity", fake_login_identity())
        resp = client.get(CALLBACK, {"code": "c", "state": st})
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.msa_identity.sub == "sub-1"


# ---- 引导页 choose ----


class TestChoosePage:
    def _prepare(self, client, monkeypatch, **opts):
        enable(**opts)
        client.get("/oauth/microsoft/login")
        st_url = client.get("/oauth/microsoft/login")
        from urllib.parse import parse_qs, urlsplit

        st = parse_qs(urlsplit(st_url["Location"]).query)["state"][0]
        monkeypatch.setattr(msauth, "fetch_login_identity", fake_login_identity())
        client.get(CALLBACK, {"code": "c", "state": st})

    def test_get_without_pending_redirects_away(self, client):
        enable()
        resp = client.get("/oauth/microsoft/choose")
        assert "过期".encode() in resp.content or resp.status_code == 200

    def test_register_off_hides_create_branch(self, client, monkeypatch):
        self._prepare(client, monkeypatch, microsoft_register_enabled="0")
        resp = client.get("/oauth/microsoft/choose")
        assert "绑定已有账号".encode() in resp.content
        # 创建表单元素不出现（JS 引用串不误伤），仅提示卡片
        assert b'id="create-form"' not in resp.content
        assert "未开放".encode() in resp.content

    def test_register_on_shows_create_branch(self, client, monkeypatch):
        self._prepare(client, monkeypatch)
        resp = client.get("/oauth/microsoft/choose")
        assert "创建新账号".encode() in resp.content

    def test_create_respects_site_registration_off(self, client, monkeypatch):
        self._prepare(client, monkeypatch, disable_registration=1)
        resp = client.post("/oauth/microsoft/choose", {"action": "create"})
        assert "关闭注册" in resp.json()["message"]
        assert User.objects.count() == 0

    def test_create_success(self, client, monkeypatch):
        self._prepare(client, monkeypatch, user_initial_score=5)
        assert not User.objects.exists()
        resp = client.post("/oauth/microsoft/choose", {"action": "create"})
        assert resp.status_code == 200
        new_user = User.objects.get(email="ms@live.com")
        assert new_user.verified is True
        assert new_user.score == 5
        assert not new_user.has_usable_password()
        assert new_user.msa_identity.sub == "sub-1"
        assert str(new_user.uid) == client.session["_auth_user_id"]

    def test_create_email_conflict_falls_to_error(self, client, user, monkeypatch):
        """Microsoft 邮箱已被站内普通注册占用 → 建号拒绝，引导走绑定分支。"""
        User.objects.create_user(
            email="ms@live.com", nickname="taken", password="password8"
        )
        self._prepare(client, monkeypatch)
        resp = client.post("/oauth/microsoft/choose", {"action": "create"})
        assert "已被注册" in resp.json()["message"]
        assert User.objects.count() == 2

    def test_create_with_invitation(self, client, monkeypatch):

        from invitation.models import InvitationCode

        code = InvitationCode.objects.create()
        self._prepare(client, monkeypatch, invitation_required=1)
        # 无效邀请码
        resp = client.post("/oauth/microsoft/choose",
                           {"action": "create", "invitation_code": "wrong"})
        assert "邀请码无效" in resp.json()["message"]
        # 有效邀请码
        resp = client.post("/oauth/microsoft/choose",
                           {"action": "create", "invitation_code": code.code})
        new_user = User.objects.get(email="ms@live.com")
        code.refresh_from_db()
        assert code.used_by == new_user
        assert code.used_at is not None
        assert resp.status_code == 200

    def test_bind_existing_wrong_password(self, client, monkeypatch, user):
        self._prepare(client, monkeypatch)
        resp = client.post("/oauth/microsoft/choose", {
            "action": "bind", "email": "u@x.com", "password": "badpass1",
        })
        assert "密码错误" in resp.json()["message"]
        assert not hasattr(user, "msa_identity")

    def test_bind_existing_success(self, client, monkeypatch, user):
        self._prepare(client, monkeypatch)
        client.post("/oauth/microsoft/choose", {
            "action": "bind", "email": "u@x.com", "password": "password8",
        })
        user.refresh_from_db()
        assert user.msa_identity.sub == "sub-1"
        assert str(user.uid) == client.session["_auth_user_id"]


# ---- 回调：正版绑定 ----


class TestGenuineCallback:
    def _start(self, client, user):
        enable()
        login(client, user)
        resp = client.get("/oauth/microsoft/genuine")
        from urllib.parse import parse_qs, urlsplit

        return parse_qs(urlsplit(resp["Location"]).query)["state"][0]

    def _fake_chain(self, monkeypatch, profile, msa_token="at"):
        def fake_exchange(client_id, client_secret, code, redirect_uri, scope):
            return {"access_token": msa_token}

        monkeypatch.setattr(msauth, "exchange_code", fake_exchange)

        def fake_xbox(token):
            return profile, "mc-at"

        monkeypatch.setattr(msauth, "xbox_authenticate", fake_xbox)

    def test_success_creates_binding(self, client, user, monkeypatch):
        profile = make_mojang_profile(skin_url="http://textures.minecraft.net/texture/sk1")
        self._fake_chain(monkeypatch, profile)
        st = self._start(client, user)
        resp = client.get(CALLBACK, {"code": "c", "state": st})
        assert resp.status_code == 200
        assert "绑定成功".encode() in resp.content
        user.refresh_from_db()
        binding = MojangBinding.objects.get(user=user)
        assert binding.mc_uuid == "a" * 32
        assert binding.mc_name == "Notch"

    def test_uuid_conflict_rejected(self, client, user, monkeypatch):
        other = User.objects.create_user(
            email="o@x.com", nickname="o", password="password8"
        )
        MojangBinding.objects.create(user=other, mc_uuid="a" * 32, mc_name="Notch")
        profile = make_mojang_profile()
        self._fake_chain(monkeypatch, profile)
        st = self._start(client, user)
        resp = client.get(CALLBACK, {"code": "c", "state": st})
        assert "已被其他站内账号绑定".encode() in resp.content
        assert not MojangBinding.objects.filter(user=user).exists()

    def test_profile_error_surfaces_message(self, client, user, monkeypatch):
        def fake_exchange(client_id, client_secret, code, redirect_uri, scope):
            return {"access_token": "at"}

        monkeypatch.setattr(msauth, "exchange_code", fake_exchange)

        def fake_xbox(token):
            raise msauth.MicrosoftAuthError(
                "该 Microsoft 账号未购买 Minecraft: Java Edition，无法绑定正版角色",
                "profile_not_purchased",
            )

        monkeypatch.setattr(msauth, "xbox_authenticate", fake_xbox)
        st = self._start(client, user)
        resp = client.get(CALLBACK, {"code": "c", "state": st})
        assert "未购买".encode() in resp.content


# ---- 解绑 ----


class TestUnbind:
    def test_msa_unbind(self, client, user):
        enable()
        MsaIdentity.objects.create(user=user, sub="sub-1")
        login(client, user)
        resp = client.post("/oauth/microsoft/msa/unbind")
        data = resp.json()
        assert data["code"] == 0
        assert not MsaIdentity.objects.filter(user=user).exists()
        from oauth_bridge.models import UnbindCooldown

        assert UnbindCooldown.objects.filter(user=user, kind="msa").exists()

    def test_msa_unbind_without_binding(self, client, user):
        enable()
        login(client, user)
        resp = client.post("/oauth/microsoft/msa/unbind")
        assert resp.json()["code"] != 0

    def test_genuine_unbind(self, client, user):
        enable()
        MojangBinding.objects.create(user=user, mc_uuid="a" * 32, mc_name="Notch")
        login(client, user)
        resp = client.post("/oauth/microsoft/genuine/unbind")
        assert resp.json()["code"] == 0
        assert not MojangBinding.objects.filter(user=user).exists()
        from oauth_bridge.models import UnbindCooldown

        assert UnbindCooldown.objects.filter(user=user, kind="genuine").exists()

    def test_genuine_unbind_requires_login(self, client):
        enable()
        resp = client.post("/oauth/microsoft/genuine/unbind")
        assert resp.status_code == 302
