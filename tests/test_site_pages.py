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

    def test_no_hardcoded_static_src(self):
        """静态资源引用必须走 {% static %}（生产指纹 URL 的前置条件）。

        真实事故：JS 用硬编码 /static/js/xxx.js —— whitenoise Manifest
        存储只对 {% static %} 渲染带 hash 的指纹 URL；硬编码路径永不变，
        浏览器/反代缓存住旧 JS，模板更新后出现"新按钮 + 旧脚本"错位
        （全部已读按钮点击无网络请求的根源）。
        """
        offenders = []
        for tpl in Path("templates").rglob("*.html"):
            text = tpl.read_text(encoding="utf-8")
            for m in re.finditer(r'src="/static/[^"]+"', text):
                offenders.append(f"{tpl}: {m.group(0)}")
        assert not offenders, f"存在硬编码静态引用：{offenders}"


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
        "/user",              # 分页改造后的仪表盘
        "/user/profile",
        "/user/players",
        "/user/closet",
        "/user/notifications",
        "/skinlib",
        "/skinlib/upload",    # 本次新增的上传页
    ])
    def test_authenticated_pages(self, logged_client, path):
        assert logged_client.get(path).status_code == 200, path

    def test_login_redirect_default_lands_on_user_center(self, logged_client):
        """LOGIN_REDIRECT_URL 的语义闭环：/user 真实渲染仪表盘内容。"""
        resp = logged_client.get("/user")
        assert resp.status_code == 200
        assert "dashboard-notifications" in resp.content.decode()

    def test_user_center_nav_links_to_split_pages(self, logged_client):
        """仪表盘侧栏导航指向分页（角色/衣柜/通知/资料）。"""
        html = logged_client.get("/user").content.decode()
        for href in ("/user/players", "/user/closet", "/user/notifications", "/user/profile"):
            assert href in html, f"导航缺 {href}"

    def test_upload_page_has_form(self, logged_client):
        """上传页提供可提交表单（API 早已存在，本次补 UI）。"""
        html = logged_client.get("/skinlib/upload").content.decode()
        assert 'enctype="multipart/form-data"' in html
        assert 'id="upload-form"' in html

    def test_upload_page_default_skin_injection(self, logged_client):
        """配置默认皮肤后上传页注入 BS_DEFAULT_SKIN（披风 3D 预览人台）。"""
        from options.models import Option
        from textures.models import Texture

        u = User.objects.get(email="u@x.com")
        # 未配置 → 不注入
        html = logged_client.get("/skinlib/upload").content.decode()
        assert "BS_DEFAULT_SKIN" not in html

        alex = Texture.objects.create(
            name="默认皮", type="alex", hash="9" * 64, size=1,
            uploader=u, public=True,
        )
        Option.objects.set("default_skin_tid", str(alex.tid))
        html = logged_client.get("/skinlib/upload").content.decode()
        assert f'window.BS_DEFAULT_SKIN = {{"url": "/textures/{alex.hash}"' in html
        assert '"model": "slim"' in html

    def test_upload_preview_alex_uses_slim_model(self):
        """上传页 alex 类型预览必须显式 loadSkin(slim)（否则宽臂渲染）。"""
        js = Path("static/js/upload.js").read_text(encoding="utf-8")
        assert 'typeSelect.value === "alex"' in js
        assert 'viewer.loadSkin(dataUrl, { model: "slim" })' in js


class TestSiteNameRendering:
    """站点名渲染回归（真实事故：BS5 改造后模板误用 request.site_name——
    context processor 注入的是顶层键而非 request 属性，全站站名变空）。"""

    def test_site_name_in_brand_title_footer(self, client):
        from options.models import Option

        Option.objects.set("site_name", "站点名回归测试")
        html = client.get("/").content.decode()

        # 1) 顶栏 brand 内渲染站名
        brand_start = html.index('class="navbar-brand')
        toggler_pos = html.index('class="navbar-toggler')
        assert "站点名回归测试" in html[brand_start:toggler_pos]

        # 2) 浏览器标题含站名
        title = html[html.index("<title>"):html.index("</title>")]
        assert "站点名回归测试" in title

        # 3) brand 不得使用会致 flex 压缩为 0 的 text-truncate
        #    （手机上站点名消失事故的第二根因）
        assert "navbar-brand" in html
        brand_cls = html[brand_start:].split('"')[1]
        assert "text-truncate" not in brand_cls

    def test_site_name_defaults_without_option(self, client, db):
        """未配置站名时回落默认值（不渲染空字符串）。"""
        html = client.get("/").content.decode()
        brand_start = html.index('class="navbar-brand')
        toggler_pos = html.index('class="navbar-toggler')
        brand = html[brand_start:toggler_pos]
        assert "Blessing Skin" in brand or "皮肤" in brand, brand[:120]


class TestNavbarDomOrder:
    def test_order_and_outside_collapse(self, client, db):
        User.objects.create_user(
            email="n@x.com", nickname="n", password="password8", verified=True
        )
        client.login(username="n@x.com", password="password8")
        html = client.get("/skinlib").content.decode()

        pos_brand = html.index('id="site-navbar"')
        pos_collapse = html.index('id="site-nav"')
        pos_right = html.index('id="theme-toggle"')
        pos_toggler = html.index('class="navbar-toggler"')
        assert pos_brand < pos_collapse < pos_right < pos_toggler

        # 右侧控件不在折叠区内
        cbody = html[pos_collapse:pos_right]
        assert "theme-toggle" not in cbody
        assert "nav-avatar" not in cbody

        # 右侧控件容器带 ms-auto（手机推右）
        assert 'class="d-flex align-items-center gap-2 ms-auto"' in html

    def test_avatar_opens_offcanvas_drawer(self, client, db):
        """头像 → 右侧 offcanvas 用户抽屉（dropdown 在手机视口会飞出屏幕，弃用）。"""
        User.objects.create_user(
            email="d@x.com", nickname="抽屉用户", password="password8", verified=True
        )
        client.login(username="d@x.com", password="password8")
        html = client.get("/").content.decode()

        # 抽屉存在且含账号信息与入口
        assert 'id="user-drawer"' in html
        assert 'class="offcanvas offcanvas-end"' in html
        assert "抽屉用户" in html
        assert 'id="nav-logout"' in html
        for href in ("/user", "/user/players", "/user/closet"):
            assert f'href="{href}"' in html

        # 头像按钮触发抽屉（不再是 dropdown）
        assert 'data-bs-toggle="offcanvas" data-bs-target="#user-drawer"' in html
        assert "dropdown-menu" not in html

        # 入口链接不得携带 data-bs-dismiss：BS5 对 <a> 的 dismiss 委托
        # 一律 preventDefault()，真实 href 的导航会被吞（跳转失效事故）
        import re as _re

        dismissing_anchors = _re.findall(
            r'<a [^>]*data-bs-dismiss="[^"]*"', html
        )
        assert not dismissing_anchors, f"入口链接误用 data-bs-dismiss：{dismissing_anchors}"

        # JS 侧隐藏逻辑存在（导航不被阻断）
        js = Path("static/js/base.js").read_text(encoding="utf-8")
        assert 'closest("#user-drawer a[href], #pm-sidebar a[href]")' in js
        assert "Offcanvas.getInstance(drawer)?.hide()" in js

    def test_admin_drawer_entry(self, client, db):
        admin = User.objects.create_user(
            email="a2@x.com", nickname="管理", password="password8",
            permission=User.Permission.ADMIN, verified=True,
        )
        _ = admin
        client.login(username="a2@x.com", password="password8")
        html = client.get("/").content.decode()
        assert 'href="/admin/"' in html  # 抽屉内管理入口

    def test_anonymous_no_drawer(self, client, db):
        """匿名访客不渲染用户抽屉。"""
        html = client.get("/").content.decode()
        assert 'id="user-drawer"' not in html
        assert 'href="/auth/login"' in html

    def test_anonymous_navbar_structure(self, client, db):
        html = client.get("/").content.decode()
        assert "{{" not in html.split("</nav>")[0].replace("{{%", ""), (
            "顶栏存在未渲染模板变量"
        )
        pos_collapse = html.index('id="site-nav"')
        pos_right = html.index('id="theme-toggle"')
        pos_toggler = html.index('class="navbar-toggler"')
        assert pos_collapse < pos_right < pos_toggler
        assert 'href="/auth/login"' in html
