"""角色管理页测试。"""

import pytest

from accounts.models import User
from players.models import Player
from textures.models import Texture

pytestmark = pytest.mark.django_db


@pytest.fixture()
def user():
    return User.objects.create_user(
        email="u@x.com", nickname="u", password="password8"
    )


@pytest.fixture()
def player(user):
    return Player.objects.create(uid=user, name="Steve")


class TestPlayerManagePage:
    def _client(self, client, user):
        client.force_login(user)
        return client

    def test_page_renders_with_player_info(self, client, user, player):
        html = self._client(client, user).get(
            f"/user/players/{player.pid}"
        ).content.decode()
        assert "Steve" in html
        assert player.uuid in html
        assert "pm-rename-form" in html
        assert "pm-delete-btn" in html

    def test_page_lists_closet_items(self, client, user, player):
        skin = Texture.objects.create(
            name="S", type="steve", hash="a" * 64, size=1,
            uploader=user, public=True,
        )
        from textures.models import ClosetItem

        ClosetItem.objects.create(owner=user, texture=skin, item_name="我的皮肤")
        html = self._client(client, user).get(
            f"/user/players/{player.pid}"
        ).content.decode()
        assert f'data-tid="{skin.tid}"' in html
        assert f"/preview/{skin.tid}?height=150" in html

    def test_other_users_player_404(self, client, user, player):
        other = User.objects.create_user(
            email="v@x.com", nickname="v", password="password8"
        )
        resp = self._client(client, other).get(f"/user/players/{player.pid}")
        assert resp.status_code == 404

    def test_unknown_pid_404(self, client, user):
        assert self._client(client, user).get("/user/players/99999").status_code == 404

    def test_manage_page_url_referenced_from_user_center(self, client, user, player):
        """角色列表页有 JS 渲染容器（管理链接由 JS 动态生成）。"""
        html = self._client(client, user).get("/user/players").content.decode()
        assert 'id="players-list"' in html
        assert "user_players.js" in html
