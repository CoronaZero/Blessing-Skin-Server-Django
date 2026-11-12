"""面板用户管理增强测试（定向）。"""

import pytest

from accounts.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture()
def admin_client(client):
    User.objects.create_user(
        email="ad@x.com", nickname="ad", password="password8",
        permission=User.Permission.ADMIN, verified=True,
    )
    client.login(username="ad@x.com", password="password8")
    return client


@pytest.fixture()
def target():
    return User.objects.create_user(email="t@x.com", nickname="t", password="old-pass")


class TestResetPassword:
    def test_reset_allows_new_password_login(self, admin_client, target):
        resp = admin_client.post(
            f"/admin/users/{target.uid}/action",
            {"action": "reset_password", "password": "brand-new-pass"},
        )
        assert resp.json()["code"] == 0
        target.refresh_from_db()
        assert target.verify_password("brand-new-pass")

    def test_weak_password_rejected(self, admin_client, target):
        resp = admin_client.post(
            f"/admin/users/{target.uid}/action",
            {"action": "reset_password", "password": "123"},
        )
        assert resp.json()["code"] != 0


class TestManualVerify:
    def test_verify_marks_verified(self, admin_client, target):
        assert target.verified is False
        resp = admin_client.post(
            f"/admin/users/{target.uid}/action", {"action": "verify"}
        )
        assert resp.json()["code"] == 0
        target.refresh_from_db()
        assert target.verified is True

    def test_unverify_switch(self, admin_client, target):
        target.verified = True
        target.save()
        resp = admin_client.post(
            f"/admin/users/{target.uid}/action", {"action": "unverify"}
        )
        assert resp.json()["code"] == 0
        target.refresh_from_db()
        assert target.verified is False

    def test_verify_button_only_for_unverified(self, admin_client, target):
        html = admin_client.get("/admin/users").content.decode()
        assert "通过验证" in html  # target 未验证 → 按钮显示
        target.verified = True
        target.save()
        html = admin_client.get("/admin/users").content.decode()
        assert html.count("通过验证") == 0  # 已验证的不再显示
