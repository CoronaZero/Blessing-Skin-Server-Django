"""邮件系统增强测试：HTML 模板双通道 / 验证邮件链接修复 / 测试命令。"""

import pytest

from accounts.models import User
from options.models import Option

pytestmark = pytest.mark.django_db


class TestVerificationLinkFix:
    """bug 修复：验证邮件不再依赖 django.sites（缺该 app 时曾 500）。"""

    def test_register_sends_verification_with_valid_link(self, client, db):
        Option.objects.set("require_verification", "1")
        Option.objects.set("site_url", "https://skin.example.com")
        resp = client.post("/auth/register", {
            "email": "new@x.com",
            "password": "password8",
            "nickname": "NewUser",
        })
        assert resp.json()["code"] == 0

        from django.core import mail

        assert len(mail.outbox) == 1
        body = mail.outbox[0].body
        # 链接用 Option 的 site_url（不再走 Site 框架）
        assert "https://skin.example.com/auth/verify/" in body

    def test_site_url_defaults_without_option(self, client, db):
        """未配置 site_url 时回落 localhost（不再抛 SiteFrame 异常）。"""
        Option.objects.set("require_verification", "1")
        resp = client.post("/auth/register", {
            "email": "d@x.com", "password": "password8", "nickname": "D",
        })
        assert resp.json()["code"] == 0
        from django.core import mail

        assert len(mail.outbox) == 1
        assert "http://localhost:8000/auth/verify/" in mail.outbox[0].body

    def test_get_site_base_url_scheme_normalization(self, db):
        from accounts.verification import get_site_base_url

        Option.objects.set("site_url", "skin.example.com")  # 无协议头
        assert get_site_base_url() == "https://skin.example.com"
        Option.objects.set("site_url", "http://skin.example.com/")
        assert get_site_base_url() == "http://skin.example.com"
        Option.objects.set("site_url", "")
        assert get_site_base_url() == "http://localhost:8000"


class TestHtmlMail:
    def test_forgot_mail_dual_channel(self, client, db):
        """找回密码邮件：multipart（html + 纯文本回落）。"""
        User.objects.create_user(
            email="f@x.com", nickname="f", password="password8"
        )
        from django.core import mail

        client.post("/auth/forgot", {"email": "f@x.com"})
        msg = mail.outbox[0]
        assert msg.alternatives, "缺少 HTML 通道"
        html = msg.alternatives[0][0]
        assert "text/html" in msg.alternatives[0][1]
        assert "重置你的密码" in html
        assert "f@x.com" in msg.to
        # 纯文本回落仍含链接
        assert "auth/reset" in msg.body

    def test_verification_mail_dual_channel(self, client, db):
        Option.objects.set("require_verification", "1")
        Option.objects.set("site_url", "https://skin.example.com")
        from django.core import mail

        client.post("/auth/register", {
            "email": "h@x.com", "password": "password8", "nickname": "H",
        })
        msg = mail.outbox[0]
        assert msg.alternatives
        assert "验证你的邮箱" in msg.alternatives[0][0]

    def test_mail_template_design_consistency(self, client, db):
        """HTML 邮件与前台同一设计系统（主色 #0052d9）。"""
        User.objects.create_user(
            email="c@x.com", nickname="c", password="password8"
        )
        from django.core import mail

        client.post("/auth/forgot", {"email": "c@x.com"})
        html = mail.outbox[0].alternatives[0][0]
        assert "#0052d9" in html
        assert "table" in html  # 邮件客户端兼容布局


class TestSendTestEmailCommand:
    def test_console_backend_reports_not_real(self, db):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("send_test_email", "target@example.com", stdout=out)
        text = out.getvalue()
        assert "不是真实发送" in text
        from django.core import mail

        assert len(mail.outbox) == 1
        assert mail.outbox[0].alternatives  # 测试邮件本身也是 HTML 双通道

    def test_invalid_recipient_rejected(self, db):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("send_test_email", "not-an-email")

    def test_shows_configured_smtp_summary(self, db):
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command

        Option.objects.set("mail_host", "smtp.example.com")
        Option.objects.set("mail_port", "465")
        Option.objects.set("mail_username", "mailer")
        out = StringIO()
        with patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=1):
            call_command("send_test_email", "target@example.com", stdout=out)
        text = out.getvalue()
        assert "smtp.example.com:465" in text
        assert "mailer" in text


class TestSetupWritesSiteUrl:
    def test_install_wizard_accepts_site_url(self, client, tmp_path, monkeypatch, db):
        import json

        from setup import views

        monkeypatch.setattr(views, "LOCK_FILE", tmp_path / "install.lock")
        resp = client.post(
            "/setup/",
            data=json.dumps({
                "site_name": "S",
                "site_url": "https://s.example.com",
                "admin_email": "a@x.com",
                "admin_password": "password8",
            }),
            content_type="application/json",
        )
        assert resp.json()["code"] == 0
        assert Option.objects.get_value("site_url") == "https://s.example.com"
