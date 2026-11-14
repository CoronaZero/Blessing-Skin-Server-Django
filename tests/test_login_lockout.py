"""登录失败锁定测试（accounts.login_guard）。

P1 修复锚点：/auth/login 与 MSA 引导绑定是等价的凭据撞库入口，
未配置验证码 provider 时须按 IP 失败计数硬锁定。
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


def _login(client, password="password8", **extra):
    return client.post("/auth/login", {
        "identification": "u@x.com",
        "password": password,
        **extra,
    }, REMOTE_ADDR="7.7.7.7")


class TestLoginLockout:
    def test_locked_after_threshold_failures(self, client, user):
        """无验证码站点连续失败达阈值后拒绝（含正确密码）。"""
        for _ in range(5):
            resp = _login(client, password="wrong-pass")
            assert resp.json()["code"] != 0
        # 第 6 次：即使凭据正确也被锁定拒绝
        resp = _login(client)
        assert "登录失败次数过多" in resp.json()["message"]
        assert "_auth_user_id" not in client.session

    def test_success_clears_counter(self, client, user):
        for _ in range(4):
            _login(client, password="wrong-pass")
        assert _login(client).json()["code"] == 0  # 未达阈值，成功登录
        client.logout()  # 成功登录即登录态：后续失败分支前登出（guest 拦截）
        for _ in range(4):  # 清零后再失败 4 次仍不锁
            assert _login(client, password="wrong-pass").json()["code"] != 0
        assert _login(client).json()["code"] == 0

    def test_lockout_is_per_ip(self, client, user):
        for _ in range(5):
            _login(client, password="wrong-pass")
        # 另一 IP 不受影响
        resp = client.post("/auth/login", {
            "identification": "u@x.com", "password": "password8",
        }, REMOTE_ADDR="8.8.8.8")
        assert resp.json()["code"] == 0

    def test_provider_enabled_delegates_to_captcha(self, client, user):
        """验证码 provider 启用时保持原版语义：由验证码承担人机校验，
        不做硬锁定（正确凭据 + 有效 token 始终可登录）。"""
        from unittest.mock import patch

        Option.objects.set("captcha_provider", "turnstile")
        for _ in range(10):
            _login(client, password="wrong-pass")
        with patch("accounts.captcha._http_verify_turnstile", return_value=True):
            resp = _login(client, captcha="good-token")
        assert resp.json()["code"] == 0


class TestChooseBindLockout:
    """MSA 引导绑定（/oauth/microsoft/choose action=bind）不得绕过锁定。"""

    def _prepare_pending(self, client, monkeypatch):
        from oauth_bridge import msauth
        from tests.test_msa_views import fake_login_identity  # noqa: F401

        Option.objects.bulk_set({
            "microsoft_oauth_enabled": "1",
            "microsoft_login_enabled": "1",
            "microsoft_client_id": "cid",
            "microsoft_client_secret": "sec",
        })
        from urllib.parse import parse_qs, urlsplit

        resp = client.get("/oauth/microsoft/login")
        st = parse_qs(urlsplit(resp["Location"]).query)["state"][0]
        monkeypatch.setattr(
            msauth, "fetch_login_identity", fake_login_identity()
        )
        assert client.get(
            "/oauth/microsoft/callback", {"code": "c", "state": st}
        ).status_code == 302

    def test_choose_bind_records_failure_and_locks(self, client, user, monkeypatch):
        self._prepare_pending(client, monkeypatch)
        for _ in range(5):
            resp = client.post("/oauth/microsoft/choose", {
                "action": "bind", "email": "u@x.com", "password": "bad-pass",
            }, REMOTE_ADDR="7.7.7.7")
            assert resp.json()["code"] != 0
        # 第 6 次：正确密码也被锁定拒绝，绑定不得完成
        resp = client.post("/oauth/microsoft/choose", {
            "action": "bind", "email": "u@x.com", "password": "password8",
        }, REMOTE_ADDR="7.7.7.7")
        assert "登录失败次数过多" in resp.json()["message"]
        user.refresh_from_db()
        assert not hasattr(user, "msa_identity")

    def test_login_failures_also_lock_choose_bind(self, client, user, monkeypatch):
        """登录入口与绑定入口共用同一计数：login 撞满后 choose 同 IP 拒绝。"""
        self._prepare_pending(client, monkeypatch)
        for _ in range(5):
            _login(client, password="wrong-pass")
        resp = client.post("/oauth/microsoft/choose", {
            "action": "bind", "email": "u@x.com", "password": "password8",
        }, REMOTE_ADDR="7.7.7.7")
        assert "登录失败次数过多" in resp.json()["message"]
        user.refresh_from_db()
        assert not hasattr(user, "msa_identity")
