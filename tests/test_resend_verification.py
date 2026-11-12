"""重发验证邮件测试（定向）。"""

import pytest

from accounts.models import User

pytestmark = pytest_mark = pytest.mark.django_db


class TestResendVerification:
    def _client(self, client, verified=False):
        User.objects.create_user(
            email="v@x.com", nickname="v", password="password8", verified=verified
        )
        client.login(username="v@x.com", password="password8")
        return client

    def test_sends_mail_to_unverified(self, client, db):
        c = self._client(client)
        resp = c.post("/user/resend-verification")
        assert resp.json()["code"] == 0
        from django.core import mail

        assert len(mail.outbox) == 1
        assert "验证你的邮箱" in mail.outbox[0].subject

    def test_rejected_if_already_verified(self, client, db):
        c = self._client(client, verified=True)
        resp = c.post("/user/resend-verification")
        assert resp.json()["code"] != 0

    def test_cooldown_blocks_rapid_resend(self, client, db):
        c = self._client(client)
        assert c.post("/user/resend-verification").json()["code"] == 0
        resp = c.post("/user/resend-verification")
        assert resp.json()["code"] != 0
        assert "频繁" in resp.json()["message"]

    def test_dashboard_shows_banner_when_unverified(self, client, db):
        c = self._client(client)
        html = c.get("/user").content.decode()
        assert "尚未验证" in html
        assert "resend-vf-btn" in html

    def test_dashboard_clean_when_verified(self, client, db):
        c = self._client(client, verified=True)
        html = c.get("/user").content.decode()
        assert "尚未验证" not in html
