"""登录跳转闭环契约：LOGIN_URL / next 参数回跳 / open redirect 防护。"""

import pytest

from accounts.models import User

pytestmark = pytest.mark.django_db


class TestLoginUrlSetting:
    def test_login_url_points_to_auth_login(self):
        """login_required 必须弹到 /auth/login（Django 默认 /accounts/login/）。"""
        from django.conf import settings

        assert settings.LOGIN_URL == "/auth/login"

    def test_login_redirect_url(self):
        from django.conf import settings

        assert settings.LOGIN_REDIRECT_URL == "/user"


class TestAnonymousRedirect:
    def test_protected_page_redirects_to_auth_login(self, client, db):
        """未登录访问 /skinlib → 302 /auth/login?next=/skinlib。"""
        resp = client.get("/skinlib")
        assert resp.status_code == 302
        assert resp["Location"].startswith("/auth/login")
        assert "next=/skinlib" in resp["Location"]

    def test_next_param_in_login_page(self, client, db):
        """登录页回显 next（隐藏域），登录后应回跳原页。"""
        resp = client.get("/auth/login?next=/skinlib")
        html = resp.content.decode()
        assert 'name="next" value="/skinlib"' in html

    def test_malicious_next_sanitized_on_page(self, client, db):
        """外站 next 不回显（防 open redirect 钓鱼）。"""
        resp = client.get("/auth/login?next=https://evil.example.net/x")
        html = resp.content.decode()
        assert "evil.example.net" not in html


class TestLoginNextRoundtrip:
    def _login(self, client, next_value=None):
        data = {
            "identification": "u@x.com",
            "password": "password8",
        }
        if next_value is not None:
            data["next"] = next_value
        return client.post("/auth/login", data)

    @pytest.fixture()
    def user(self):
        return User.objects.create_user(
            email="u@x.com", nickname="u", password="password8"
        )

    def test_login_returns_to_next(self, client, user):
        """登录成功后回跳 next 指定的受保护页。"""
        resp = self._login(client, next_value="/skinlib")
        assert resp.json()["code"] == 0
        assert resp.json()["data"]["redirectTo"] == "/skinlib"

    def test_login_without_next_goes_default(self, client, user):
        resp = self._login(client)
        assert resp.json()["data"]["redirectTo"] == "/user"

    def test_external_next_ignored(self, client, user):
        """外站绝对地址 next 忽略（open redirect 防护）。"""
        resp = self._login(client, next_value="https://evil.example.net/x")
        assert resp.json()["data"]["redirectTo"] == "/user"

    def test_protocol_relative_next_ignored(self, client, user):
        """//evil.com 协议相对地址忽略（浏览器会当外站处理）。"""
        resp = self._login(client, next_value="//evil.example.net")
        assert resp.json()["data"]["redirectTo"] == "/user"


class TestGuestOnly:
    """已登录用户访问 guest 页（登录/注册）重定向。

    对照原版 guest middleware（RedirectIfAuthenticated → redirect('user')）：
    login/register 四路由原版均套 guest 组；forgot/reset 不拦（保持原版）。
    """

    @pytest.fixture()
    def user(self):
        return User.objects.create_user(
            email="u@x.com", nickname="u", password="password8"
        )

    def _logged_in(self, client, user):
        client.force_login(user)
        return client

    def test_logged_in_get_login_redirects(self, client, user):
        resp = self._logged_in(client, user).get("/auth/login")
        assert resp.status_code == 302
        assert resp["Location"] == "/user"

    def test_logged_in_login_respects_safe_next(self, client, user):
        """next 为站内路径时跳 next，外站 next 回落 /user。"""
        c = self._logged_in(client, user)
        assert c.get("/auth/login?next=/skinlib")["Location"] == "/skinlib"
        assert (
            c.get("/auth/login?next=https://evil.example.net/x")["Location"]
            == "/user"
        )

    def test_logged_in_post_login_returns_redirect_to(self, client, user):
        """POST 拦截返回与登录成功同形态的 JSON（前端 bsPost 自然跳转），
        且不执行任何登录逻辑。"""
        resp = self._logged_in(client, user).post(
            "/auth/login", {"identification": "u@x.com", "password": "wrong"}
        )
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["redirectTo"] == "/user"

    def test_logged_in_register_page_redirects(self, client, user):
        resp = self._logged_in(client, user).get("/auth/register")
        assert resp.status_code == 302
        assert resp["Location"] == "/user"

    def test_logged_in_post_register_blocked(self, client, user):
        resp = self._logged_in(client, user).post(
            "/auth/register", {"email": "n@x.com", "password": "password8"}
        )
        assert resp.json()["data"]["redirectTo"] == "/user"
        assert not User.objects.filter(email="n@x.com").exists()

    def test_anonymous_still_sees_login_page(self, client, db):
        """匿名用户不受影响（回归）。"""
        assert client.get("/auth/login").status_code == 200

    def test_forgot_still_accessible_logged_in(self, client, user):
        """forgot/reset 不在原版 guest 组，已登录可访问（锁定该语义）。"""
        assert self._logged_in(client, user).get("/auth/forgot").status_code == 200
