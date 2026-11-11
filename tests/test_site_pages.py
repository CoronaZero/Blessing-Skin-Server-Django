"""全站页面可达性：模板 URL 审计 + 关键页面冒烟。

背景（真实部署踩坑）：导航栏/登录跳转/LOGIN_REDIRECT_URL 全指向
/user，但该路由从未实现——用户登录后直接 404。此类"引用悬空"
没有测试拦截，故建两层防护：
1. 模板审计：提取所有模板 href="/..." 逐个 resolve()
2. 冒烟清单：用户主路径页面全部可达
"""

import re
from pathlib import Path

import pytest
from django.urls import resolve

from accounts.models import User

pytestmark = pytest.mark.django_db


class TestTemplateUrlAudit:
    def test_all_template_hrefs_resolve(self):
        """模板里每个静态 href 都必须有路由（悬空链接 CI 红灯）。

        动态路径前缀（/textures/{{ hash }} 截取为 /textures/）用
        「拼任意尾段可解析」规则容忍。
        """

        def resolves(path: str) -> bool:
            try:
                resolve(path)
                return True
            except Exception:
                try:
                    resolve(path + "x")  # 动态路由前缀
                    return True
                except Exception:
                    return False

        unresolved = []
        for tpl in Path("templates").rglob("*.html"):
            text = tpl.read_text(encoding="utf-8")
            for m in re.finditer(r'(?:href|action|data-skin)="(/[^"{#"?]*)', text):
                path = m.group(1)
                if path.startswith("/static/"):
                    continue
                if not resolves(path):
                    unresolved.append(f"{tpl}: {path}")
        assert not unresolved, f"悬空链接：{unresolved}"

    def test_login_settings_urls_resolve(self):
        """settings 里的登录跳转常量必须真实可达。"""
        from django.conf import settings

        resolve(settings.LOGIN_URL)
        resolve(settings.LOGIN_REDIRECT_URL)


class TestCorePagesSmoke:
    """关键页面冒烟：匿名可达页 + 登录后可达页。"""

    @pytest.fixture()
    def logged_client(self, client):
        User.objects.create_user(
            email="u@x.com", nickname="u", password="password8"
        )
        client.login(username="u@x.com", password="password8")
        return client

    @pytest.mark.parametrize("path", [
        "/",
        "/auth/login",
        "/auth/register",
        "/auth/forgot",
        "/auth/reset",
        "/healthz",
        "/api/yggdrasil/",
    ])
    def test_anonymous_pages(self, client, path):
        assert client.get(path).status_code == 200, path

    @pytest.mark.parametrize("path", [
        "/user",              # 本次修复的用户中心主页
        "/user/profile",
        "/skinlib",
        "/skinlib/upload",    # 本次新增的上传页
        "/admin/login/",
    ])
    def test_authenticated_pages(self, logged_client, path):
        assert logged_client.get(path).status_code == 200, path

    def test_login_redirect_default_lands_on_user_center(self, logged_client):
        """LOGIN_REDIRECT_URL 的语义闭环：/user 真实渲染用户中心内容。"""
        resp = logged_client.get("/user")
        assert resp.status_code == 200
        assert "id=\"players-list\"" in resp.content.decode()

    def test_profile_page_anchor_ids_match_nav_links(self, logged_client):
        """锚点一致性：导航 /user#players 等锚点在页面真实存在。"""
        html = logged_client.get("/user").content.decode()
        for anchor in ("players", "closet", "notifications", "profile"):
            assert f'id="{anchor}"' in html, f"锚点 #{anchor} 缺失"

    def test_upload_page_has_form(self, logged_client):
        """上传页提供可提交表单（API 早已存在，本次补 UI）。"""
        html = logged_client.get("/skinlib/upload").content.decode()
        assert 'enctype="multipart/form-data"' in html
        assert 'id="upload-form"' in html
