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
