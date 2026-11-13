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
