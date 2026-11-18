"""登录验证码强制执行测试。

provider 启用时登录无条件强制（此前仅失败 3 次后校验，
页面显示 widget 却可无 token 登录）。CF 与 Google 共用同一入口。
"""

import pytest

from accounts.models import User
from options.models import Option

pytestmark = pytest.mark.django_db


@pytest.fixture()
def user():
    return User.objects.create_user(
        email="u@x.com", nickname="u", password="password8"
    )


class TestLoginCaptchaEnforced:
    def _login(self, client, **extra):
        return client.post("/auth/login", {
            "identification": "u@x.com",
            "password": "password8",
            **extra,
        })

    def test_turnstile_enabled_blocks_login_without_token(self, client, user):
        Option.objects.set("captcha_provider", "turnstile")
        resp = self._login(client)  # 凭据正确但无验证码
        assert resp.json()["code"] != 0, "无 token 不应登录成功"

    def test_turnstile_enabled_blocks_even_after_fail_streak(self, client, user):
        """此前该场景靠失败计数兜底——现在首次登录即拦。"""
        Option.objects.set("captcha_provider", "turnstile")
        from unittest.mock import patch

        with patch("accounts.captcha._http_verify_turnstile", return_value=False):
            resp = self._login(client, captcha="fake-token")
        assert resp.json()["code"] != 0

    @pytest.mark.parametrize("provider", ["turnstile", "recaptcha"])
    def test_valid_token_allows_login(self, client, user, provider):
        """CF 与 Google 两个 provider 凭真实校验通过后正常登录。"""
        from unittest.mock import patch

        Option.objects.set("captcha_provider", provider)
        with (
            patch("accounts.captcha._http_verify_turnstile", return_value=True) as t,
            patch("accounts.captcha._http_verify_recaptcha", return_value=True) as r,
        ):
            resp = self._login(client, captcha="good-token")
        assert resp.json()["code"] == 0
        assert (t.call_count + r.call_count) == 1  # 只调当前 provider

    def test_captcha_disabled_login_still_works(self, client, user):
        """关键回归：验证码关闭的站点不带 token 必须能登录。"""
        resp = self._login(client)
        assert resp.json()["code"] == 0

    def test_wrong_password_rejected_even_with_valid_captcha(self, client, user):
        from unittest.mock import patch

        Option.objects.set("captcha_provider", "turnstile")
        with patch("accounts.captcha._http_verify_turnstile", return_value=True):
            resp = self._login(client, password="wrong-pass", captcha="t")
        assert resp.json()["code"] != 0

    def test_register_still_enforced(self, client, db):
        """注册路径的既有强制不受本次改动影响。"""
        Option.objects.set("captcha_provider", "turnstile")
        resp = client.post("/auth/register", {
            "email": "new@x.com", "password": "password8", "nickname": "N",
        })
        assert resp.json()["code"] != 0


class TestCaptchaResetAfterFailure:
    """验证码 token 一次性：表单提交失败后前端必须重置 widget。

    真实事故：登录密码输错后 CF Turnstile 已被消费，widget 不重置，
    用户必须手动刷新页面才能再次提交。
    """

    def test_base_js_defines_reset_helper(self):
        from pathlib import Path

        js = Path("static/js/base.js").read_text(encoding="utf-8")
        assert "window.bsCaptchaReset" in js
        assert "turnstile.reset" in js
        assert "grecaptcha.reset" in js

    @pytest.mark.parametrize("page", [
        "/auth/login",
        "/auth/register",
        "/auth/forgot",
    ])
    def test_auth_pages_reset_on_failure(self, client, page):
        html = client.get(page).content.decode()
        assert "bsCaptchaReset" in html, f"{page} 失败分支未调用验证码重置"

    def test_msa_choose_page_resets_on_failure(self):
        from pathlib import Path

        tpl = Path(
            "templates/oauth_bridge/choose.html"
        ).read_text(encoding="utf-8")
        assert "bsCaptchaReset" in tpl
        # forgot 页旧的内联重置逻辑须已迁移到统一 helper（不留双份）
        forgot = Path("templates/accounts/forgot.html").read_text(encoding="utf-8")
        assert "turnstile.reset" not in forgot
        assert "bsCaptchaReset" in forgot
