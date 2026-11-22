"""面板角色管理测试（定向）。"""

import pytest

from accounts.models import User
from players.models import Player

pytestmark = pytest.mark.django_db


@pytest.fixture()
def admin_client(client):
    User.objects.create_user(
        email="ad@x.com", nickname="ad", password="password8",
        permission=User.Permission.ADMIN,
    )
    client.login(username="ad@x.com", password="password8")
    return client


class TestPanelPlayers:
    def test_lists_all_users_players(self, admin_client):
        owner = User.objects.create_user(email="o@x.com", nickname="o", password="x")
        Player.objects.create(uid=owner, name="Someone")
        html = admin_client.get("/admin/players").content.decode()
        assert "Someone" in html
        assert "o@x.com" in html

    def test_search_by_name(self, admin_client):
        owner = User.objects.create_user(email="s@x.com", nickname="s", password="x")
        Player.objects.create(uid=owner, name="Alpha")
        Player.objects.create(uid=owner, name="Beta")
        html = admin_client.get("/admin/players?q=Alpha").content.decode()
        assert "Alpha" in html
        assert "Beta" not in html

    def test_delete_player(self, admin_client):
        owner = User.objects.create_user(email="d@x.com", nickname="d", password="x")
        p = Player.objects.create(uid=owner, name="DelMe")
        resp = admin_client.post(f"/admin/players/{p.pid}/delete")
        assert resp.json()["code"] == 0
        assert not Player.objects.filter(pid=p.pid).exists()

    def test_plain_admin_cannot_operate_admin_player(self, client):
        super_u = User.objects.create_user(
            email="sp@x.com", nickname="sp", password="x",
            permission=User.Permission.SUPER_ADMIN, verified=True,
        )
        target = Player.objects.create(uid=super_u, name="SuperGuy")
        c = _plain_admin(client)
        resp = c.post(f"/admin/players/{target.pid}/delete")
        assert resp.json()["code"] != 0
        assert "\u7ba1\u7406\u5458" in resp.json()["message"]
        # UI 上也不应出现操作按钮
        html = c.get("/admin/players").content.decode()
        assert "\u53ea\u8bfb" in html


class TestSuperAdminCanOperateAdminPlayers:
    """角色操作分级（用户需求锁定）：超管可改管理员的角色、
    管理员动不了任何管理员/超管角色、其他超管对超管互相只读。"""

    def _super_client(self, client):
        User.objects.create_user(
            email="root@x.com", nickname="root", password="password8",
            permission=User.Permission.SUPER_ADMIN, verified=True,
        )
        client.login(username="root@x.com", password="password8")
        return client

    def test_super_sees_buttons_for_admin_player(self, client):
        """超管视角：管理员的角色行出现操作按钮（此前模板一律只读）。"""
        admin_u = User.objects.create_user(
            email="ad2@x.com", nickname="ad2", password="x",
            permission=User.Permission.ADMIN,
        )
        Player.objects.create(uid=admin_u, name="AdminGuy")
        c = self._super_client(client)
        html = c.get("/admin/players?q=AdminGuy").content.decode()
        assert "AdminGuy" in html
        assert "data-set-skin" in html
        assert "data-set-cape" in html

    def test_super_sees_readonly_for_other_super(self, client):
        """超管视角：其他超管的角色仍然只读（后端 _can_operate 同款自我保护）。"""
        other_super = User.objects.create_user(
            email="root2@x.com", nickname="root2", password="x",
            permission=User.Permission.SUPER_ADMIN,
        )
        Player.objects.create(uid=other_super, name="OtherSuperGuy")
        c = self._super_client(client)
        html = c.get("/admin/players?q=OtherSuperGuy").content.decode()
        assert "OtherSuperGuy" in html
        # 按钮形态为 data-set-skin="<pid>"；JS 选择器字面量不计入
        assert 'data-set-skin="' not in html
        assert "超管角色（只读）" in html

    def test_super_can_set_skin_on_admin_player(self, client):
        """端到端：超管对管理员的角色设置皮肤成功（后端本就放行，前端补齐）。"""
        from textures.models import Texture

        admin_u = User.objects.create_user(
            email="ad3@x.com", nickname="ad3", password="x",
            permission=User.Permission.ADMIN,
        )
        target = Player.objects.create(uid=admin_u, name="AdminSkinTarget")
        skin = Texture.objects.create(
            name="S", type="steve", hash="4" * 64, size=1,
            uploader=admin_u, public=True,
        )
        c = self._super_client(client)
        resp = c.post(f"/admin/players/{target.pid}/skin", {"tid": str(skin.tid)})
        assert resp.json()["code"] == 0
        target.refresh_from_db()
        assert target.tid_skin == skin

    def test_super_cannot_operate_other_super(self, client):
        other_super = User.objects.create_user(
            email="root3@x.com", nickname="root3", password="x",
            permission=User.Permission.SUPER_ADMIN,
        )
        target = Player.objects.create(uid=other_super, name="NoTouchGuy")
        c = self._super_client(client)
        resp = c.post(f"/admin/players/{target.pid}/delete")
        assert resp.json()["code"] != 0
        assert Player.objects.filter(pid=target.pid).exists()

    def test_plain_admin_still_readonly_for_admin_players(self, client):
        """管理员视角不变：管理员的角色行仍只读。"""
        admin_u = User.objects.create_user(
            email="ad4@x.com", nickname="ad4", password="x",
            permission=User.Permission.ADMIN,
        )
        Player.objects.create(uid=admin_u, name="AdminGuy2")
        c = _plain_admin(client)
        html = c.get("/admin/players?q=AdminGuy2").content.decode()
        assert "AdminGuy2" in html
        # 按钮形态为 data-set-skin="<pid>"；JS 选择器字面量不计入
        assert 'data-set-skin="' not in html


    def test_set_skin_and_cape(self, client):
        from textures.models import Texture

        normal = User.objects.create_user(email="t@x.com", nickname="t", password="x")
        target = Player.objects.create(uid=normal, name="Target")
        skin = Texture.objects.create(
            name="S", type="steve", hash="1" * 64, size=1,
            uploader=normal, public=True,
        )
        cape = Texture.objects.create(
            name="C", type="cape", hash="2" * 64, size=1,
            uploader=normal, public=True,
        )
        c = _plain_admin(client)
        resp = c.post(f"/admin/players/{target.pid}/skin", {"tid": str(skin.tid)})
        assert resp.json()["code"] == 0
        target.refresh_from_db()
        assert target.tid_skin == skin
        resp = c.post(f"/admin/players/{target.pid}/cape", {"tid": str(cape.tid)})
        assert resp.json()["code"] == 0
        target.refresh_from_db()
        assert target.tid_cape == cape
        resp = c.post(f"/admin/players/{target.pid}/skin", {"tid": "0"})
        assert resp.json()["code"] == 0
        target.refresh_from_db()
        assert target.tid_skin is None

    def test_skin_cape_type_mismatch(self, client):
        from textures.models import Texture

        normal = User.objects.create_user(email="tm@x.com", nickname="tm", password="x")
        target = Player.objects.create(uid=normal, name="Mismatch")
        cape = Texture.objects.create(
            name="C2", type="cape", hash="3" * 64, size=1,
            uploader=normal, public=True,
        )
        c = _plain_admin(client)
        resp = c.post(f"/admin/players/{target.pid}/skin", {"tid": str(cape.tid)})
        assert resp.json()["code"] != 0
        assert "\u7c7b\u578b\u4e0d\u7b26" in resp.json()["message"]

    def test_normal_user_rejected(self, client):
        User.objects.create_user(email="n@x.com", nickname="n", password="x")
        client.force_login(User.objects.get(email="n@x.com"))
        assert client.get("/admin/players").status_code == 404

def _plain_admin(client):
    from accounts.models import User

    User.objects.create_user(
        email="pa@x.com", nickname="pa", password="password8",
        permission=User.Permission.ADMIN, verified=True,
    )
    client.login(username="pa@x.com", password="password8")
    return client
