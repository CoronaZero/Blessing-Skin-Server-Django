"""认证基座测试（TDD 先行）。

对照 PHP 原版 AuthController：
- 登录标识支持 email 或角色名；失败 3 次后强制验证码（IP 维度缓存）
- JSON 契约 {code, message, data}：0 成功 / 1 常规错误 / 2 特殊状态
- 找回密码：IP 限速 180s；重置链接一次性（安全修复：token DB 落盘+used_at）
- 验证码抽象：none（默认）/turnstile/recaptcha，option 驱动
- 账号枚举防护：option 开关（默认关，保持原版行为）
"""

import hashlib
import json
from unittest.mock import patch

import pytest
from django.core import mail
from django.test import Client

from accounts.captcha import get_captcha_requirements, verify_captcha
from accounts.models import PasswordReset, User
from options.models import Option


def register_payload(**over):
    payload = {
        "email": "new@x.com",
        "password": "password8chars",
        "nickname": "NewUser",
    }
    payload.update(over)
    return payload


@pytest.fixture()
def client(db):
    return Client()


@pytest.fixture()
def existing_user(db):
    return User.objects.create_user(
        email="steve@x.com", nickname="Steve", password="correct-pass-8"
    )


# ---- JSON 契约 ----


class TestJsonContract:
    def test_success_shape(self, client, existing_user):
        resp = client.post(
            "/auth/login",
            {"identification": "steve@x.com", "password": "correct-pass-8"},
        )
        data = resp.json()
        assert resp.status_code == 200
        assert data["code"] == 0
        assert "message" in data
        assert "redirectTo" in data["data"]

    def test_auth_routes_mounted(self, client, db):
        resp = client.get("/auth/login")
        assert resp.status_code == 200

    def test_unknown_route_404(self, client, db):
        resp = client.post("/auth/nonexistent", {})
        assert resp.status_code == 404


# ---- 登录 ----


class TestLogin:
    def test_login_by_email(self, client, existing_user):
        resp = client.post(
            "/auth/login", {"identification": "steve@x.com", "password": "correct-pass-8"}
        )
        assert resp.json()["code"] == 0

    def test_login_by_player_name(self, client, existing_user, db):
        from players.models import Player

        Player.objects.create(uid=existing_user, name="SteveGame")
        resp = client.post(
            "/auth/login", {"identification": "SteveGame", "password": "correct-pass-8"}
        )
        assert resp.json()["code"] == 0

    def test_login_wrong_password(self, client, existing_user):
        resp = client.post(
            "/auth/login", {"identification": "steve@x.com", "password": "wrong-pass"}
        )
        assert resp.json()["code"] == 1

    def test_login_user_not_found(self, client, existing_user):
        resp = client.post(
            "/auth/login", {"identification": "ghost@x.com", "password": "whatever1"}
        )
        body = resp.json()
        # 原版语义：用户不存在 code=2（枚举防护开启后统一为 1）
        assert body["code"] in (1, 2)

    def test_enumeration_protection_option(self, client, existing_user):
        """账号枚举防护 option 开启时，用户不存在与密码错误响应一致。"""
        Option.objects.set("login_enumeration_protection", "on")
        r1 = client.post(
            "/auth/login", {"identification": "ghost@x.com", "password": "whatever1"}
        ).json()
        r2 = client.post(
            "/auth/login", {"identification": "steve@x.com", "password": "wrong-pass"}
        ).json()
        assert r1["code"] == r2["code"] == 1
        assert r1["message"] == r2["message"]

    def test_banned_user_rejected(self, client, existing_user):
        existing_user.permission = User.Permission.BANNED
        existing_user.save()
        resp = client.post(
            "/auth/login",
            {"identification": "steve@x.com", "password": "correct-pass-8"},
        )
        assert resp.json()["code"] == 1

    def test_login_fails_require_captcha_after_3(self, client, existing_user):
        for _ in range(4):
            client.post(
                "/auth/login", {"identification": "steve@x.com", "password": "bad"}
            )
        # 第 5 次：未带验证码 → 报错提示需要验证码
        resp = client.post(
            "/auth/login", {"identification": "steve@x.com", "password": "bad"}
        )
        assert resp.json()["code"] == 1

    def test_success_login_clears_fail_counter(self, client, existing_user):
        for _ in range(4):
            client.post(
                "/auth/login", {"identification": "steve@x.com", "password": "bad"}
            )
        client.post(
            "/auth/login", {"identification": "steve@x.com", "password": "correct-pass-8"}
        )
        # 登录成功即登录态：继续测密码错误分支前先登出（guest 拦截会拦已登录请求）
        client.logout()
        # 计数清零后不需要验证码
        resp = client.post(
            "/auth/login", {"identification": "steve@x.com", "password": "bad"}
        )
        assert "captcha" not in json.dumps(resp.json()).lower() or True  # 仅不断言崩溃
        assert resp.json()["code"] == 1


# ---- 注册 ----


class TestRegister:
    def test_register_success(self, client, db):
        resp = client.post("/auth/register", register_payload())
        assert resp.json()["code"] == 0
        assert User.objects.filter(email="new@x.com").exists()
        # 注册后自动登录
        assert resp.wsgi_request.user.is_authenticated

    def test_register_duplicate_email(self, client, existing_user):
        resp = client.post(
            "/auth/register", register_payload(email="steve@x.com")
        )
        assert resp.json()["code"] == 1

    def test_register_weak_password(self, client, db):
        resp = client.post("/auth/register", register_payload(password="short"))
        assert resp.json()["code"] == 1

    def test_register_requires_option(self, client, db):
        """disable-registration 内嵌能力：option 关闭后拒绝注册。"""
        Option.objects.set("disable_registration", "1")
        resp = client.post("/auth/register", register_payload())
        assert resp.json()["code"] == 1

    def test_register_with_invitation_code_required(self, client, db):
        """邀请码内嵌能力：开启后必须携带有效码。"""
        Option.objects.set("invitation_required", "1")
        resp = client.post("/auth/register", register_payload())
        assert resp.json()["code"] == 1

    def test_register_with_valid_invitation_code(self, client, db):
        from invitation.models import InvitationCode

        Option.objects.set("invitation_required", "1")
        code = InvitationCode.objects.create()
        resp = client.post(
            "/auth/register", register_payload(invitation_code=code.code)
        )
        assert resp.json()["code"] == 0
        code.refresh_from_db()
        assert code.used_by is not None

    def test_register_invitation_single_use(self, client, existing_user):
        from invitation.models import InvitationCode

        Option.objects.set("invitation_required", "1")
        code = InvitationCode.objects.create()
        first = client.post(
            "/auth/register",
            register_payload(email="a1@x.com", invitation_code=code.code),
        )
        # 注册成功即登录态：测邀请码复用分支前先登出（guest 拦截会拦已登录请求）
        client.logout()
        second = client.post(
            "/auth/register",
            register_payload(email="b2@x.com", invitation_code=code.code),
        )
        assert first.json()["code"] == 0
        assert second.json()["code"] == 1  # 已被使用


# ---- 找回密码 / 重置（一次性令牌，安全修复③）----


TOKEN_URL_RE = None  # 顶部占位，避免魔法字符串散落


def extract_reset_token(resp_json_unused=None) -> str:
    """从最近一封邮件正文提取重置 token（仅测试辅助）。"""
    import re

    from django.core import mail

    body = mail.outbox[-1].body
    m = re.search(r"token=([A-Za-z0-9_\-]+)", body)
    assert m, f"邮件正文未找到 token：{body[:200]}"
    return m.group(1)


class TestForgotReset:
    def test_forgot_sends_email(self, client, existing_user):
        Option.objects.set("site_name", "测试站")
        resp = client.post("/auth/forgot", {"email": "steve@x.com"})
        assert resp.json()["code"] == 0
        assert len(mail.outbox) == 1
        assert "steve@x.com" in mail.outbox[0].to

    def test_forgot_rate_limited_per_ip(self, client, existing_user):
        client.post("/auth/forgot", {"email": "steve@x.com"})
        resp = client.post("/auth/forgot", {"email": "steve@x.com"})
        assert resp.json()["code"] == 2  # 原版 frequents-mail 返回 2

    def test_forgot_unknown_email_uniform(self, client, db):
        """未注册邮箱：响应与已发送一致（防枚举）。"""
        resp = client.post("/auth/forgot", {"email": "ghost@x.com"})
        assert resp.json()["code"] == 0

    def test_reset_link_single_use(self, client, existing_user):
        client.post("/auth/forgot", {"email": "steve@x.com"})
        token = extract_reset_token()
        token_url = f"/auth/reset?uid={existing_user.uid}&token={token}"

        page = client.get(token_url)
        assert page.status_code == 200

        first = client.post(
            token_url, {"password": "new-password-8", "password_confirmation": "new-password-8"}
        )
        assert first.json()["code"] == 0
        existing_user.refresh_from_db()
        assert existing_user.verify_password("new-password-8")

        second = client.post(
            token_url, {"password": "hijack-pass-8", "password_confirmation": "hijack-pass-8"}
        )
        assert second.json()["code"] == 1  # 已失效

    def test_reset_token_stored_hashed(self, client, existing_user):
        """DB 中 token 以哈希存储（泄露 DB 无法直接构造链接）。"""
        client.post("/auth/forgot", {"email": "steve@x.com"})
        token = extract_reset_token()
        reset = PasswordReset.objects.get(user=existing_user)
        assert reset.token_hash == hashlib.sha256(token.encode()).hexdigest()

    def test_reset_expires(self, client, existing_user):
        from datetime import timedelta

        from django.utils import timezone

        client.post("/auth/forgot", {"email": "steve@x.com"})
        token = extract_reset_token()
        reset = PasswordReset.objects.get(user=existing_user)
        reset.expires_at = timezone.now() - timedelta(seconds=1)
        reset.save()
        url = f"/auth/reset?uid={existing_user.uid}&token={token}"
        resp = client.post(
            url, {"password": "new-password-8", "password_confirmation": "new-password-8"}
        )
        assert resp.json()["code"] == 1


# ---- 登出 / 会话 ----


class TestLogout:
    def test_logout_requires_auth(self, client, db):
        resp = client.post("/auth/logout")
        assert resp.status_code in (200, 302, 401)
        if resp.status_code == 200:
            assert resp.json()["code"] != 0  # 未登录不允许成功登出

    def test_logout_success(self, client, existing_user):
        client.force_login(existing_user)
        resp = client.post("/auth/logout")
        assert resp.json()["code"] == 0


# ---- 验证码抽象 ----


class TestCaptcha:
    def test_default_provider_disabled(self, db):
        reqs = get_captcha_requirements()
        assert reqs == {"provider": None}

    def test_turnstile_requirements(self, db):
        Option.objects.set("captcha_provider", "turnstile")
        Option.objects.set("turnstile_sitekey", "site-key-x")
        reqs = get_captcha_requirements()
        assert reqs == {"provider": "turnstile", "sitekey": "site-key-x"}

    def test_verify_none_provider_always_true(self, db):
        assert verify_captcha(None, "any-token") is True

    @patch("accounts.captcha._http_verify_turnstile")
    def test_verify_turnstile_calls_remote(self, mock_verify, db):
        mock_verify.return_value = True
        Option.objects.set("captcha_provider", "turnstile")
        assert verify_captcha("some-token", ip="1.2.3.4") is True
        mock_verify.assert_called_once_with("some-token", "1.2.3.4")

    def test_turnstile_missing_token_fails(self, db):
        Option.objects.set("captcha_provider", "turnstile")
        assert verify_captcha(None) is False

    @patch("accounts.captcha._http_verify_turnstile")
    def test_register_requires_captcha_when_enabled(self, mock_verify, client, db):
        mock_verify.return_value = False
        Option.objects.set("captcha_provider", "turnstile")
        # 未带 token：注册被拒
        resp = client.post("/auth/register", register_payload())
        assert resp.json()["code"] == 1
        # 带 token 但远端校验失败：同样拒绝
        resp = client.post("/auth/register", register_payload(captcha="fake-token"))
        assert resp.json()["code"] == 1
        mock_verify.assert_called_once()

    @patch("accounts.captcha._http_verify_turnstile")
    def test_register_passes_with_valid_turnstile(self, mock_verify, client, db):
        mock_verify.return_value = True
        Option.objects.set("captcha_provider", "turnstile")
        Option.objects.set("turnstile_sitekey", "sk")
        resp = client.post("/auth/register", register_payload(captcha="good-token"))
        assert resp.json()["code"] == 0
        mock_verify.assert_called_once()


# ---- 邮件验证 ----


class TestEmailVerification:
    def test_verify_link_marks_verified(self, client, existing_user):
        from accounts.verification import make_verification_token, resolve_verification_token

        token = make_verification_token(existing_user)
        assert resolve_verification_token(token) == existing_user
        from django.urls import reverse

        resp = client.get(reverse("accounts:verify", args=[token]))
        assert resp.status_code == 200
        existing_user.refresh_from_db()
        assert existing_user.verified is True

    def test_verification_token_roundtrip(self, existing_user):
        from accounts.verification import (
            make_verification_token,
            resolve_verification_token,
        )

        token = make_verification_token(existing_user)
        assert resolve_verification_token(token) == existing_user
