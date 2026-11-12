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

    def test_normal_user_rejected(self, client):
        User.objects.create_user(email="n@x.com", nickname="n", password="x")
        client.force_login(User.objects.get(email="n@x.com"))
        assert client.get("/admin/players").status_code == 404
