"""Admin 注册契约测试：核心模型必须可管理、权限语义正确。"""

import pytest
from django.contrib import admin as django_admin
from django.test import Client

from accounts.models import User

pytestmark = pytest.mark.django_db


class TestAdminRegistrations:
    def test_all_models_registered(self):

        from accounts.models import PasswordReset
        from invitation.models import InvitationCode
        from moderation.models import Report
        from notifications.models import Notification
        from options.models import Option
        from players.models import Player
        from textures.models import ClosetItem, Texture
        from yggdrasil.models import YggdrasilLog, YggdrasilToken

        registry = django_admin.site._registry
        expected = [
            User,
            PasswordReset,
            Player,
            Texture,
            ClosetItem,
            Report,
            Notification,
            Option,
            InvitationCode,
            YggdrasilToken,
            YggdrasilLog,
        ]
        for model in expected:
            assert model in registry, f"{model.__name__} 未注册到 Admin"

    def test_admin_index_requires_permission(self, client):
        resp = client.get("/admin/")
        assert resp.status_code == 302  # 未登录跳登录页

    def test_super_admin_can_access(self, client):
        User.objects.create_superuser(email="op@x.com", nickname="OP", password="password8")
        assert client.login(username="op@x.com", password="password8")
        resp = client.get("/admin/")
        assert resp.status_code == 200

    def test_normal_user_cannot_access(self, client):
        User.objects.create_user(email="n@x.com", nickname="n", password="password8")
        assert client.login(username="n@x.com", password="password8")
        resp = client.get("/admin/")
        # is_staff=False → 仍拒绝
        assert resp.status_code in (302, 403)


class TestAdminListPages:
    @pytest.fixture()
    def op_client(self):
        User.objects.create_superuser(email="op2@x.com", nickname="OP2", password="password8")
        c = Client()
        assert c.login(username="op2@x.com", password="password8")
        return c

    @pytest.mark.parametrize("path", [
        "/admin/accounts/user/",
        "/admin/players/player/",
        "/admin/textures/texture/",
        "/admin/moderation/report/",
        "/admin/options/option/",
        "/admin/invitation/invitationcode/",
        "/admin/yggdrasil/yggdrasiltoken/",
        "/admin/yggdrasil/yggdrasillog/",
    ])
    def test_core_changelists_render(self, op_client, path):
        resp = op_client.get(path)
        assert resp.status_code == 200, f"{path} 返回 {resp.status_code}"

    def test_user_ban_action(self, op_client):
        user = User.objects.create_user(
            email="banme@x.com", nickname="ban", password="password8"
        )
        resp = op_client.post(
            "/admin/accounts/user/",
            {"action": "ban_users", "_selected_action": [user.uid]},
        )
        assert resp.status_code in (200, 302)
        user.refresh_from_db()
        assert user.permission == User.Permission.BANNED
