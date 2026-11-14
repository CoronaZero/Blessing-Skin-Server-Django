"""Microsoft OAuth UI 集成测试：登录/注册页按钮、我的角色分区、资料页绑定区。"""

import pytest

from accounts.models import User
from oauth_bridge.models import MojangBinding, MsaIdentity
from options.models import Option

pytestmark = pytest.mark.django_db


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


@pytest.fixture()
def user():
    return User.objects.create_user(
        email="u@x.com", nickname="u", password="password8", verified=True
    )


class TestAuthPageButtons:
    def test_login_button_hidden_when_off(self, client):
        resp = client.get("/auth/login")
        assert b"/oauth/microsoft/login" not in resp.content

    def test_login_button_shown_when_ready(self, client):
        enable()
        resp = client.get("/auth/login")
        assert b"/oauth/microsoft/login" in resp.content

    def test_register_button_hidden_when_register_off(self, client):
        enable(microsoft_register_enabled="0")
        resp = client.get("/auth/register")
        assert b"/oauth/microsoft/login" not in resp.content

    def test_register_button_shown_when_ready(self, client):
        enable()
        resp = client.get("/auth/register")
        assert b"/oauth/microsoft/login" in resp.content


class TestPlayersPageSection:
    def _get(self, client, user):
        client.force_login(user)
        return client.get("/user/players")

    def test_section_hidden_when_disabled(self, client, user):
        resp = self._get(client, user)
        assert b"/oauth/microsoft/genuine" not in resp.content
        assert "正版角色".encode() not in resp.content

    def test_bind_entry_shown_when_ready(self, client, user):
        enable()
        resp = self._get(client, user)
        assert b"/oauth/microsoft/genuine" in resp.content
        assert "绑定正版角色".encode() in resp.content

    def test_bound_player_card(self, client, user):
        enable()
        binding = MojangBinding.objects.create(
            user=user, mc_uuid="a" * 32, mc_name="Notch"
        )
        resp = self._get(client, user)
        assert b"Notch" in resp.content
        assert (binding.mc_uuid).encode() in resp.content
        assert b"genuine-unbind-btn" in resp.content
        # 已绑定：不再显示绑定入口
        assert b'href="/oauth/microsoft/genuine"' not in resp.content

    def test_bound_card_shown_even_if_disabled(self, client, user):
        """已绑定用户在开关关闭后仍可看到只读角色与解绑入口。"""
        MojangBinding.objects.create(user=user, mc_uuid="a" * 32, mc_name="Notch")
        resp = self._get(client, user)
        assert b"Notch" in resp.content
        assert b"genuine-unbind-btn" in resp.content


class TestProfilePageBinding:
    def _get(self, client, user):
        client.force_login(user)
        return client.get("/user/profile")

    def test_bind_entry_when_ready(self, client, user):
        enable()
        resp = self._get(client, user)
        assert b"/oauth/microsoft/login" in resp.content

    def test_bound_identity_shown(self, client, user):
        enable()
        MsaIdentity.objects.create(
            user=user, sub="sub-1", email="ms@live.com", display_name="MS"
        )
        resp = self._get(client, user)
        assert b"ms@live.com" in resp.content
        assert b"msa-unbind-btn" in resp.content

    def test_bound_identity_no_rebind_entry(self, client, user):
        enable()
        MsaIdentity.objects.create(user=user, sub="sub-1", email="ms@live.com")
        resp = self._get(client, user)
        assert b'href="/oauth/microsoft/login"' not in resp.content

    def test_admin_profile_msa_present_without_delete_form(self, client):
        """管理员资料页：注销表单不渲染，Microsoft 区须仍在。

        user_profile.js 顺序绑定事件，注销表单缺失曾中断后续绑定
        （管理员点「解绑 Microsoft 账号」无任何反应）——此组合锁定触发条件。
        """
        admin = User.objects.create_user(
            email="adm@x.com", nickname="adm", password="password8",
            permission=User.Permission.ADMIN, verified=True,
        )
        MsaIdentity.objects.create(user=admin, sub="sub-a", email="a@live.com")
        client.force_login(admin)
        resp = client.get("/user/profile")
        assert b"msa-unbind-btn" in resp.content
        assert b'id="delete-form"' not in resp.content
