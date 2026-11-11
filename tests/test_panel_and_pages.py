"""自研管理面板 + 分页用户中心 + 默认材质测试。"""

import pytest

from accounts.models import User
from options.models import Option
from players.models import Player
from textures.models import Texture

pytestmark = pytest.mark.django_db


def _admin(permission=User.Permission.ADMIN):
    return User.objects.create_user(
        email=f"p{permission}@x.com", nickname=f"p{permission}",
        password="password8", permission=permission,
    )


class TestPanelPermissions:
    def test_dashboard_rejects_normal_user(self, client, db):
        user = User.objects.create_user(email="n@x.com", nickname="n", password="x")
        client.force_login(user)
        # 已登录但非管理员：wrapper 拒绝
        assert client.get("/admin/").status_code == 404

    def test_dashboard_allows_admin(self, client, db):
        client.force_login(_admin())
        assert client.get("/admin/").status_code == 200

    def test_options_hidden_from_normal_admin(self, client, db):
        client.force_login(_admin(User.Permission.ADMIN))
        assert client.get("/admin/options").status_code == 404

    def test_options_visible_to_super_admin(self, client, db):
        client.force_login(_admin(User.Permission.SUPER_ADMIN))
        assert client.get("/admin/options").status_code == 200

    def test_user_perm_change_requires_super(self, client, db):
        target = User.objects.create_user(email="t@x.com", nickname="t", password="x")
        client.force_login(_admin(User.Permission.ADMIN))
        resp = client.post(f"/admin/users/{target.uid}/action",
                           {"action": "perm", "level": "1"})
        assert resp.json()["code"] != 0


class TestPanelOptionsSave:
    def test_save_bool_int_text(self, client, db):
        client.force_login(_admin(User.Permission.SUPER_ADMIN))
        resp = client.post("/admin/options/save", {
            "site_name": "测试站",
            "disable_registration": "1",
            "regs_per_ip": "7",
            "mail_host": "smtp.test.com",
            "mail_password": "",  # 留空不改
        })
        assert resp.json()["code"] == 0
        assert Option.objects.get_value("site_name") == "测试站"
        assert Option.objects.get_value("disable_registration") == "1"
        assert Option.objects.get_value("regs_per_ip") == "7"
        assert Option.objects.get_value("mail_host") == "smtp.test.com"

    def test_unknown_key_ignored(self, client, db):
        client.force_login(_admin(User.Permission.SUPER_ADMIN))
        resp = client.post("/admin/options/save", {"evil_key": "x"})
        assert resp.json()["code"] == 0
        assert Option.objects.get_value("evil_key") is None


class TestDefaultTextures:
    def test_create_player_applies_defaults(self, client, db):
        user = User.objects.create_user(email="u@x.com", nickname="u", password="x")
        skin = Texture.objects.create(
            name="S", type="steve", hash="a" * 64, size=1, uploader=user, public=True)
        Option.objects.set("default_skin_tid", str(skin.tid))
        client.force_login(user)
        resp = client.post("/user/players", {"name": "NewGuy"})
        assert resp.json()["code"] == 0
        player = Player.objects.get(name="NewGuy")
        assert player.tid_skin == skin

    def test_manage_page_shows_default_placeholder(self, client, db):
        user = User.objects.create_user(email="u2@x.com", nickname="u2", password="x")
        cape = Texture.objects.create(
            name="C", type="cape", hash="b" * 64, size=1, uploader=user, public=True)
        Option.objects.set("default_cape_tid", str(cape.tid))
        player = Player.objects.create(uid=user, name="NoCapeGuy")
        client.force_login(user)
        html = client.get(f"/user/players/{player.pid}").content.decode()
        assert f"/preview/{cape.tid}" in html
        assert "默认披风占位" in html


class TestUserCenterSplitPages:
    @pytest.fixture()
    def logged(self, client):
        User.objects.create_user(email="u@x.com", nickname="u", password="password8")
        client.login(username="u@x.com", password="password8")
        return client

    @pytest.mark.parametrize("path,expect_fragment", [
        ("/user", "dashboard-notifications"),          # 仪表盘
        ("/user/profile", "nick-form"),                 # 资料页
        ("/user/players", "add-player-form"),           # 角色页（同 URL 浏览器直达）
        ("/user/closet", "closet-list"),                # 衣柜页
        ("/user/notifications", "notifications-list"),  # 通知页
    ])
    def test_split_pages_render(self, logged, path, expect_fragment):
        """浏览器直达（无 XHR 头）= 页面 HTML，不再是 JSON。"""
        html = logged.get(path).content.decode()
        assert expect_fragment in html

    @pytest.mark.parametrize("path", [
        "/user/players",
        "/user/closet",
        "/user/notifications",
    ])
    def test_ajax_gets_json(self, logged, path):
        """JS fetch（XHR 头）= JSON API，与页面同 URL 协商。"""
        resp = logged.get(
            path, headers={"X-Requested-With": "XMLHttpRequest"}
        )
        assert resp["Content-Type"] == "application/json"
        assert resp.json()["code"] == 0

    def test_nav_has_avatar_and_logout(self, logged):
        html = logged.get("/user").content.decode()
        assert "nav-avatar" in html
        assert "nav-logout" in html
        assert "退出登录" in html


class TestOptionsSectionSplit:
    @pytest.fixture()
    def super_client(self, client):
        User.objects.create_superuser(
            email="op@x.com", nickname="OP", password="password8"
        )
        client.login(username="op@x.com", password="password8")
        return client

    @pytest.mark.parametrize("section,expect,absent", [
        ("general", "site_name", "mail_host"),           # 常规不含 SMTP
        ("users", "disable_registration", "mail_host"),
        ("services", "mail_host", "site_name"),          # 对接服务不含站名
        ("yggdrasil", "ygg_token_expire_1", "mail_host"),
    ])
    def test_sections_split(self, super_client, section, expect, absent):
        html = super_client.get(f"/admin/options/{section}").content.decode()
        assert expect in html
        assert absent not in html

    def test_unknown_section_falls_back_general(self, super_client):
        assert super_client.get("/admin/options/nope").status_code == 200

    def test_yggdrasil_tab_shows_public_key(self, super_client):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        from options.models import Option

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        Option.objects.set("ygg_private_key", key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode())
        html = super_client.get("/admin/options/yggdrasil").content.decode()
        assert "BEGIN PUBLIC KEY" in html


class TestUploadPreviewHook:
    def test_upload_page_has_local_preview_canvas(self, client, db):
        User.objects.create_user(email="up@x.com", nickname="up", password="x")
        client.force_login(User.objects.get(email="up@x.com"))
        html = client.get("/skinlib/upload").content.decode()
        assert "upload-viewer" in html
        assert "skinview3d.bundle.js" in html
