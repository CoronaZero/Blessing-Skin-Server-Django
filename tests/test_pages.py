"""页面渲染冒烟测试：认证页面 + 模板上下文。"""

import pytest

from accounts.models import User
from options.models import Option

pytestmark = pytest.mark.django_db


class TestAuthPages:
    def test_login_page_renders_captcha_slot_when_turnstile(
        self, client, db
    ):
        Option.objects.set("captcha_provider", "turnstile")
        Option.objects.set("turnstile_sitekey", "pk-test")
        resp = client.get("/auth/login")
        html = resp.content.decode()
        assert "captcha-slot" in html
        assert "pk-test" in html
        # 静态资源本地引用（禁止 CDN）
        assert "/static/css/base.css" in html
        assert "cdn.jsdelivr" not in html
        assert "unpkg.com" not in html

    def test_login_page_no_captcha_by_default(self, client, db):
        resp = client.get("/auth/login")
        assert "captcha-slot" not in resp.content.decode()

    def test_register_page_shows_invitation_field(self, client, db):
        Option.objects.set("invitation_required", "1")
        resp = client.get("/auth/register")
        assert "邀请码" in resp.content.decode()

    def test_reset_page_carries_token(self, client, db):
        user = User.objects.create_user(
            email="r@x.com", nickname="r", password="password8"
        )
        resp = client.get("/auth/reset?uid=1&token=abc")
        assert 'name="token" value="abc"' in resp.content.decode()
        _ = user

    def test_base_template_no_third_party_cdn(self, client, db):
        """静态资源全部本地提供（约束：禁 CDN）。"""
        resp = client.get("/auth/login")
        html = resp.content.decode()
        assert "cdnjs" not in html and "googleapis" not in html
        assert "jsdelivr" not in html and "unpkg" not in html
