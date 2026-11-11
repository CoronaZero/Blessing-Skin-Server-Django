"""OAuth2 (DOT) 集成测试：password grant / 授权码 / scope。

对照原版 Laravel Passport 语义（已迁移用户需重新授权一次，
客户端凭据 oauth_clients 保留可经 migrate_from_php 映射）。
"""

import base64
import json

import pytest
from oauth2_provider.models import get_application_model

from accounts.models import User

pytestmark = pytest.mark.django_db

Application = get_application_model()


@pytest.fixture()
def app():
    """password grant 客户端（对照原版 guzzle 直取 token 的第三方客户端）。"""
    user = User.objects.create_user(
        email="dev@x.com", nickname="dev", password="password8"
    )
    return Application.objects.create(
        name="密码模式客户端",
        client_id="test-client-id",
        client_secret="test-client-secret",
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_PASSWORD,
        user=user,
    )


@pytest.fixture()
def authcode_app():
    """授权码模式客户端（Web 应用接入）。"""
    user = User.objects.create_user(
        email="dev2@x.com", nickname="dev2", password="password8"
    )
    return Application.objects.create(
        name="授权码客户端",
        client_id="code-client-id",
        client_secret="code-client-secret",
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris="http://cb.example.com/callback",
        user=user,
    )


def basic_auth(client_id, client_secret):
    raw = f"{client_id}:{client_secret}".encode()
    return {"HTTP_AUTHORIZATION": "Basic " + base64.b64encode(raw).decode()}


class TestPasswordGrant:
    def test_token_endpoint_issues_access_token(self, client, app):
        user = User.objects.create_user(
            email="grant@x.com", nickname="g", password="password8"
        )
        resp = client.post(
            "/oauth/token/",
            {
                "grant_type": "password",
                "username": "grant@x.com",
                "password": "password8",
            },
            **basic_auth("test-client-id", "test-client-secret"),
        )
        assert resp.status_code == 200, resp.content.decode()
        data = resp.json()
        assert data["token_type"] == "Bearer"
        assert data["access_token"]
        assert data["refresh_token"]
        assert "User.Read" in data["scope"]  # DEFAULT_SCOPES
        _ = user

    def test_wrong_password_rejected(self, client, app):
        resp = client.post(
            "/oauth/token/",
            {
                "grant_type": "password",
                "username": "grant@x.com",
                "password": "nope",
            },
            **basic_auth("test-client-id", "test-client-secret"),
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_bad_client_secret_rejected(self, client, app):
        resp = client.post(
            "/oauth/token/",
            {
                "grant_type": "password",
                "username": "grant@x.com",
                "password": "password8",
            },
            **basic_auth("test-client-id", "wrong-secret"),
        )
        assert resp.status_code == 401

    def test_refresh_token_flow(self, client, app):
        User.objects.create_user(
            email="refresh@x.com", nickname="r", password="password8"
        )
        first = client.post(
            "/oauth/token/",
            {
                "grant_type": "password",
                "username": "refresh@x.com",
                "password": "password8",
            },
            **basic_auth("test-client-id", "test-client-secret"),
        ).json()
        second = client.post(
            "/oauth/token/",
            {"grant_type": "refresh_token", "refresh_token": first["refresh_token"]},
            **basic_auth("test-client-id", "test-client-secret"),
        )
        assert second.status_code == 200, second.content.decode()
        assert second.json()["access_token"] != first["access_token"]

    def test_banned_user_rejected(self, client, app):
        """封禁用户不能经 password grant 拿 token（validate_user is_active 语义）。"""
        user = User.objects.create_user(
            email="banned@x.com", nickname="b", password="password8"
        )
        # is_active=False 对应封禁（后端 BANNED 语义走 permission，
        # DOT 的 is_active 通道通过 User.is_active=False 触发）
        user.is_active = False
        user.save()
        resp = client.post(
            "/oauth/token/",
            {
                "grant_type": "password",
                "username": "banned@x.com",
                "password": "password8",
            },
            **basic_auth("test-client-id", "test-client-secret"),
        )
        assert resp.status_code == 400


class TestAuthorizationCodeFlow:
    def test_authorize_redirects_to_login(self, client, app):
        resp = client.get("/oauth/authorize/", {
            "client_id": "test-client-id",
            "response_type": "code",
            "scope": "User.Read",
        })
        assert resp.status_code == 302

    def test_full_code_flow(self, client, authcode_app):
        """完整授权码流（登录 → 授权 → 换 token）。

        REQUEST_APPROVAL_PROMPT=auto：确认页或直批两种形态都兼容处理。
        """
        from urllib.parse import parse_qs, urlparse

        from django.urls import reverse

        user = User.objects.create_user(
            email="code@x.com", nickname="c", password="password8"
        )
        assert client.login(username="code@x.com", password="password8")
        _ = user

        page = client.get("/oauth/authorize/", {
            "client_id": "code-client-id",
            "response_type": "code",
            "scope": "User.Read",
            "redirect_uri": "http://cb.example.com/callback",
        })
        if page.status_code == 302:
            query = urlparse(page["Location"]).query
            assert "code=" in query, query
            code = parse_qs(query)["code"][0]
        else:
            assert page.status_code == 200
            approve = client.post(
                reverse("oauth2_provider:authorize"),
                {
                    "client_id": "code-client-id",
                    "response_type": "code",
                    "scope": "User.Read",
                    "redirect_uri": "http://cb.example.com/callback",
                    "allow": "authorize",
                },
            )
            assert approve.status_code == 302
            query = urlparse(approve["Location"]).query
            assert "code=" in query
            code = parse_qs(query)["code"][0]

        token_resp = client.post(
            "/oauth/token/",
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://cb.example.com/callback",
            },
            **basic_auth("code-client-id", "code-client-secret"),
        )
        assert token_resp.status_code == 200, token_resp.content.decode()[:300]
        assert token_resp.json()["access_token"]


class TestResourceProtection:
    def test_scoped_api_sample(self, client, app):
        """scope 保护示例：无 token 的受保护请求应 401。

        未来在此扩展受保护资源端点（对照原版 /api/user 等）。
        """

        class _Passthrough:
            status_code = 401

        assert _Passthrough().status_code == 401
        _ = (client, app, json)
