"""签到 CSRF 与验证码测试端点测试。"""

import pytest

from accounts.models import User

pytestmark = pytest.mark.django_db


class TestSignCsrfViaCookie:
    def test_sign_works_without_form_token(self, client, db):
        """签到不在表单内 → CSRF 走 cookie 通道（bsCsrfToken cookie 回退）。"""
        from datetime import timedelta

        from django.utils import timezone

        user = User.objects.create_user(
            email="s@x.com", nickname="s", password="password8"
        )
        user.last_sign_at = timezone.now() - timedelta(days=2)
        user.save()
        client.force_login(user)
        resp = client.post("/user/sign")
        assert resp.json()["code"] == 0


class TestCaptchaTestEndpoint:
    @pytest.fixture()
    def super_client(self, client):
        User.objects.create_superuser(
            email="op@x.com", nickname="OP", password="password8"
        )
        client.login(username="op@x.com", password="password8")
        return client

    def test_rejects_unknown_provider(self, super_client):
        resp = super_client.post("/admin/options/captcha-test", {"provider": "nope"})
        assert resp.json()["code"] != 0

    def test_rejects_missing_secret(self, super_client):
        resp = super_client.post(
            "/admin/options/captcha-test", {"provider": "turnstile"}
        )
        assert resp.json()["code"] != 0
        assert "Sitekey" in resp.json()["message"] or "Secret" in resp.json()["message"]

    def test_normal_admin_forbidden(self, client):
        User.objects.create_user(
            email="ad@x.com", nickname="ad", password="password8",
            permission=User.Permission.ADMIN,
        )
        client.login(username="ad@x.com", password="password8")
        resp = client.post(
            "/admin/options/captcha-test", {"provider": "turnstile"}
        )
        assert resp.status_code == 404
