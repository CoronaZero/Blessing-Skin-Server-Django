"""HTTPS 反代部署契约（CSRF Origin / 代理头 / Secure Cookie）。"""

import re

import pytest
from django.test import Client

from accounts.models import User

pytestmark = pytest.mark.django_db


def load_base(monkeypatch, tmp_path, **env):
    import importlib

    env_file = env.pop("__env_file", None)
    monkeypatch.setenv("ENV_FILE", str(env_file) if env_file else str(tmp_path / "no.env"))
    for key in ("SECRET_KEY", "DEBUG", "ALLOWED_HOSTS", "COOKIE_SECURE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    import config.settings.base as base

    return importlib.reload(base)


class TestCookieSecureFromEnvFile:
    def test_cookie_secure_readable_from_dotenv_file(self, monkeypatch, tmp_path):
        """.env 文件通道：COOKIE_SECURE=1 写入 .env 即生效。"""
        env_file = tmp_path / ".env"
        env_file.write_text("COOKIE_SECURE=1\n", encoding="utf-8")
        base = load_base(monkeypatch, tmp_path, __env_file=str(env_file))
        assert base.SESSION_COOKIE_SECURE is True
        assert base.CSRF_COOKIE_SECURE is True

    def test_real_env_takes_precedence_over_dotenv(self, monkeypatch, tmp_path):
        """同名真实环境变量优先于 .env（override=False 语义）。"""
        env_file = tmp_path / ".env"
        env_file.write_text("COOKIE_SECURE=1\n", encoding="utf-8")
        base = load_base(
            monkeypatch, tmp_path,
            __env_file=str(env_file), COOKIE_SECURE="0",
        )
        assert base.SESSION_COOKIE_SECURE is False


class TestTrustedOriginsSettings:
    def test_derived_from_allowed_hosts(self, monkeypatch, tmp_path):
        base = load_base(
            monkeypatch, tmp_path,
            SECRET_KEY="k", ALLOWED_HOSTS="skin.example.com, localhost",
        )
        assert "https://skin.example.com" in base.CSRF_TRUSTED_ORIGINS
        assert "http://skin.example.com" in base.CSRF_TRUSTED_ORIGINS
        assert "https://localhost" in base.CSRF_TRUSTED_ORIGINS

    def test_proxy_ssl_header_configured(self, monkeypatch, tmp_path):
        """识别反代 X-Forwarded-Proto（无该头的环境不受影响）。"""
        base = load_base(monkeypatch, tmp_path, SECRET_KEY="k")
        assert base.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")

    def test_cookie_secure_default_off(self, monkeypatch, tmp_path):
        base = load_base(monkeypatch, tmp_path, SECRET_KEY="k")
        assert base.SESSION_COOKIE_SECURE is False
        assert base.CSRF_COOKIE_SECURE is False

    def test_cookie_secure_env_optin(self, monkeypatch, tmp_path):
        base = load_base(monkeypatch, tmp_path, SECRET_KEY="k", COOKIE_SECURE="1")
        assert base.SESSION_COOKIE_SECURE is True
        assert base.CSRF_COOKIE_SECURE is True


class TestCsrfOriginBehavior:
    """行为级验证：登录 POST 的 Origin 校验（enforce_csrf_checks=True）。"""

    def _post_login(self, csrf_client, origin):
        page = csrf_client.get("/auth/login")
        html = page.content.decode()
        m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
        assert m, "登录页缺少 CSRF token"
        return csrf_client.post(
            "/auth/login",
            data={
                "csrfmiddlewaretoken": m.group(1),
                "identification": "u@x.com",
                "password": "password8",
            },
            headers={"Origin": origin},
        )

    @pytest.fixture()
    def user(self):
        return User.objects.create_user(
            email="u@x.com", nickname="u", password="password8"
        )

    def _client(self):
        # ALLOWED_HOSTS 覆盖为站点域名后，client 必须用同名 SERVER_NAME
        return Client(enforce_csrf_checks=True, SERVER_NAME="skin.example.com")

    def test_trusted_https_origin_accepted(self, settings, user):
        settings.ALLOWED_HOSTS = ["skin.example.com"]
        settings.CSRF_TRUSTED_ORIGINS = ["https://skin.example.com"]
        resp = self._post_login(self._client(), "https://skin.example.com")
        assert resp.status_code == 200
        assert resp.json()["code"] == 0

    def test_untrusted_origin_rejected(self, settings, user):
        """放行信任域的同时，跨站 Origin 依然被拒（CSRF 防护核心不弱化）。"""
        settings.ALLOWED_HOSTS = ["skin.example.com"]
        settings.CSRF_TRUSTED_ORIGINS = ["https://skin.example.com"]
        resp = self._post_login(self._client(), "https://evil.example.net")
        assert resp.status_code == 403
