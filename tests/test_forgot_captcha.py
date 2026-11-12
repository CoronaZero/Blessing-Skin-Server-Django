"""忘记密码页验证码渲染（死锁修复回归）。"""

import pytest

from accounts.models import User
from options.models import Option

pytestmark = pytest.mark.django_db


class TestForgotCaptchaWidget:
    def test_turnstile_slot_rendered(self, client, db):
        Option.objects.set("captcha_provider", "turnstile")
        Option.objects.set("turnstile_sitekey", "pk-x")
        html = client.get("/auth/forgot").content.decode()
        assert "captcha-slot" in html
        assert "pk-x" in html
        assert "turnstile.js" in html

    def test_no_widget_when_disabled(self, client, db):
        html = client.get("/auth/forgot").content.decode()
        assert 'id="captcha-slot"' not in html  # JS 字符串除外，只看真实 widget

    def test_forgot_flow_with_valid_captcha(self, client, db):
        from unittest.mock import patch

        Option.objects.set("captcha_provider", "turnstile")
        User.objects.create_user(email="f@x.com", nickname="f", password="password8")
        with patch("accounts.captcha._http_verify_turnstile", return_value=True):
            resp = client.post("/auth/forgot", {"email": "f@x.com", "captcha": "t"})
        assert resp.json()["code"] == 0
        from django.core import mail

        assert len(mail.outbox) == 1
