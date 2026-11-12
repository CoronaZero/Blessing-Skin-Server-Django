"""面板 SMTP 测试邮件端点测试。"""

import pytest

from accounts.models import User
from options.models import Option

pytestmark = pytest.mark.django_db


@pytest.fixture()
def super_client(client):
    User.objects.create_superuser(
        email="op@x.com", nickname="OP", password="password8"
    )
    client.login(username="op@x.com", password="password8")
    return client


class TestPanelMailTestEndpoint:
    def test_rejects_invalid_email(self, super_client):
        resp = super_client.post("/admin/options/mail-test", {"email": "not-an-email"})
        assert resp.json()["code"] != 0

    def test_sends_via_locmem(self, super_client):
        from django.core import mail

        Option.objects.set("site_name", "测试站")
        resp = super_client.post(
            "/admin/options/mail-test", {"email": "dest@example.com"}
        )
        assert resp.json()["code"] == 0, resp.json()
        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.to == ["dest@example.com"]
        assert "SMTP 配置测试" in msg.subject
        assert msg.alternatives  # HTML 双通道

    def test_reports_failure_when_smtp_rejects(self, super_client):
        from unittest.mock import patch

        Option.objects.set("mail_host", "smtp.example.com")
        with patch("accounts.mailer.send_option_mail", return_value=0):
            resp = super_client.post(
                "/admin/options/mail-test", {"email": "dest@example.com"}
            )
        assert resp.json()["code"] != 0
        assert "SMTP 拒绝" in resp.json()["message"]

    def test_console_backend_hint_when_no_host(self, super_client):
        from unittest.mock import patch

        with patch("accounts.mailer.send_option_mail", return_value=0):
            resp = super_client.post(
                "/admin/options/mail-test", {"email": "dest@example.com"}
            )
        assert resp.json()["code"] != 0
        assert "mail_host" in resp.json()["message"]

    def test_normal_admin_forbidden(self, client):
        User.objects.create_user(
            email="ad@x.com", nickname="ad", password="password8",
            permission=User.Permission.ADMIN,
        )
        client.login(username="ad@x.com", password="password8")
        resp = client.post(
            "/admin/options/mail-test", {"email": "x@example.com"}
        )
        assert resp.status_code == 404
