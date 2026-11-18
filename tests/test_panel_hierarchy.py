"""面板权限层级测试（定向）。"""

import pytest

from accounts.models import User

pytestmark = pytest.mark.django_db


def _client(client, permission):
    email = {1: "ad@x.com", 2: "op@x.com"}[permission]
    User.objects.create_user(
        email=email, nickname=email[:2], password="password8",
        permission=permission, verified=True,
    )
    client.login(username=email, password="password8")
    return client


@pytest.fixture()
def super_admin():
    return User.objects.create_user(
        email="super@x.com", nickname="super", password="x",
        permission=User.Permission.SUPER_ADMIN,
    )


@pytest.fixture()
def other_admin():
    return User.objects.create_user(
        email="admin2@x.com", nickname="admin2", password="x",
        permission=User.Permission.ADMIN,
    )


class TestPermissionHierarchy:
    def test_plain_admin_cannot_ban_admin(self, client, other_admin):
        c = _client(client, User.Permission.ADMIN)
        resp = c.post(f"/admin/users/{other_admin.uid}/action", {"action": "ban"})
        assert resp.json()["code"] != 0
        assert "管理员" in resp.json()["message"]

    def test_plain_admin_cannot_reset_super_password(self, client, super_admin):
        c = _client(client, User.Permission.ADMIN)
        resp = c.post(
            f"/admin/users/{super_admin.uid}/action",
            {"action": "reset_password", "password": "hacked-pass"},
        )
        assert resp.json()["code"] != 0

    def test_plain_admin_cannot_ban_super(self, client, super_admin):
        c = _client(client, User.Permission.ADMIN)
        resp = c.post(f"/admin/users/{super_admin.uid}/action", {"action": "ban"})
        assert resp.json()["code"] != 0

    def test_super_cannot_ban_another_super(self, client, super_admin):
        other = User.objects.create_user(
            email="super2@x.com", nickname="s2", password="x",
            permission=User.Permission.SUPER_ADMIN,
        )
        c = _client(client, User.Permission.SUPER_ADMIN)
        resp = c.post(f"/admin/users/{other.uid}/action", {"action": "ban"})
        assert resp.json()["code"] != 0
        assert "超级管理员" in resp.json()["message"]

    def test_super_can_ban_plain_admin(self, client, other_admin):
        c = _client(client, User.Permission.SUPER_ADMIN)
        resp = c.post(f"/admin/users/{other_admin.uid}/action", {"action": "ban"})
        assert resp.json()["code"] == 0

    def test_cannot_reset_own_password_via_panel(self, client):
        c = _client(client, User.Permission.SUPER_ADMIN)
        me = User.objects.get(email="op@x.com")
        resp = c.post(
            f"/admin/users/{me.uid}/action",
            {"action": "reset_password", "password": "self-pass-8"},
        )
        assert resp.json()["code"] != 0

    def test_plain_admin_manages_normal_user_ok(self, client):
        normal = User.objects.create_user(email="n@x.com", nickname="n", password="x")
        c = _client(client, User.Permission.ADMIN)
        resp = c.post(f"/admin/users/{normal.uid}/action", {"action": "ban"})
        assert resp.json()["code"] == 0


class TestSuperOnlySidebarEntries:
    """超管专属侧边栏入口（站点选项 / Yggdrasil 日志）不得对普通管理员渲染。

    真实事故：邀请码页上下文硬编码 is_super=True，普通管理员在该页
    看到超管专属入口（点进去才被 404 拦截，UI 层权限泄露）。
    """

    SUPER_LINKS = (b'href="/admin/options"', b'href="/admin/yggdrasil/logs"')

    @pytest.mark.parametrize("page", [
        "/admin/invitations",
        "/admin/players",
        "/admin/",          # 对照组
    ])
    def test_plain_admin_sees_no_super_entries(self, client, page):
        c = _client(client, User.Permission.ADMIN)
        html = c.get(page).content
        for link in self.SUPER_LINKS:
            assert link not in html, f"{page} 泄露超管入口 {link}"

    @pytest.mark.parametrize("page", [
        "/admin/invitations",
        "/admin/players",
    ])
    def test_super_admin_sees_super_entries(self, client, page):
        c = _client(client, User.Permission.SUPER_ADMIN)
        html = c.get(page).content
        for link in self.SUPER_LINKS:
            assert link in html, f"{page} 缺超管入口 {link}"
