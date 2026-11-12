"""详情页衣柜双态操作测试（加入 ⇄ 移出）。

权限要点：移出端点按 owner 过滤，只能操作本人衣柜项；
按钮状态由服务端渲染（已收藏 → "移出衣柜"态）。
"""

import pytest

from accounts.models import User
from textures.models import ClosetItem, Texture

pytestmark = pytest.mark.django_db


@pytest.fixture()
def texture(db):
    owner = User.objects.create_user(
        email="up@x.com", nickname="up", password="password8", verified=True
    )
    return Texture.objects.create(
        name="T", type="steve", hash="5" * 64, size=1, uploader=owner, public=True
    )


@pytest.fixture()
def alice():
    return User.objects.create_user(
        email="a@x.com", nickname="a", password="password8", verified=True
    )


@pytest.fixture()
def bob():
    return User.objects.create_user(
        email="b@x.com", nickname="b", password="password8", verified=True
    )


def _login(client, email):
    client.login(username=email, password="password8")
    return client


class TestShowPageClosetState:
    def test_not_in_closet_shows_add(self, client, texture, alice):
        _login(client, alice.email)
        html = client.get(f"/skinlib/show?tid={texture.tid}").content.decode()
        assert "加入衣柜" in html
        assert "移出衣柜" not in html
        assert 'data-item-id=""' in html

    def test_in_closet_shows_remove_with_item_id(self, client, texture, alice):
        item = ClosetItem.objects.create(owner=alice, texture=texture)
        _login(client, alice.email)
        html = client.get(f"/skinlib/show?tid={texture.tid}").content.decode()
        assert "移出衣柜" in html
        assert "加入衣柜" not in html
        assert f'data-item-id="{item.id}"' in html

    def test_other_user_closet_does_not_leak(self, client, texture, alice, bob):
        """他人衣柜状态不影响自己：bob 已收藏但 alice 页面仍显示加入。"""
        ClosetItem.objects.create(owner=bob, texture=texture)
        _login(client, alice.email)
        html = client.get(f"/skinlib/show?tid={texture.tid}").content.decode()
        assert "加入衣柜" in html
        assert "移出衣柜" not in html


class TestClosetAddResponse:
    def test_add_returns_item_id(self, client, texture, alice):
        _login(client, alice.email)
        resp = client.post("/user/closet", {"tid": str(texture.tid)}).json()
        assert resp["code"] == 0
        item = ClosetItem.objects.get(owner=alice, texture=texture)
        assert resp["data"]["item_id"] == item.id


class TestClosetRemovePermission:
    def test_remove_own_item(self, client, texture, alice):
        item = ClosetItem.objects.create(owner=alice, texture=texture)
        _login(client, alice.email)
        resp = client.post(f"/user/closet/{item.id}/remove").json()
        assert resp["code"] == 0
        assert not ClosetItem.objects.filter(id=item.id).exists()

    def test_cannot_remove_others_item(self, client, texture, alice, bob):
        """别人的衣柜项 id → 报不存在，且 bob 的衣柜不受影响。"""
        item = ClosetItem.objects.create(owner=bob, texture=texture)
        _login(client, alice.email)
        resp = client.post(f"/user/closet/{item.id}/remove").json()
        assert resp["code"] == 1
        assert "不存在" in resp["message"]
        assert ClosetItem.objects.filter(id=item.id).exists()

    def test_anonymous_redirected(self, client, db, texture):
        resp = client.post("/user/closet/1/remove")
        assert resp.status_code == 302  # login_required 跳登录


class TestAddRemoveCycle:
    def test_full_cycle(self, client, texture, alice):
        """加入 → 页面显示移出 → 移出 → 页面恢复加入。"""
        _login(client, alice.email)

        added = client.post("/user/closet", {"tid": str(texture.tid)}).json()
        assert added["code"] == 0
        item_id = added["data"]["item_id"]

        html = client.get(f"/skinlib/show?tid={texture.tid}").content.decode()
        assert "移出衣柜" in html

        removed = client.post(f"/user/closet/{item_id}/remove").json()
        assert removed["code"] == 0

        html2 = client.get(f"/skinlib/show?tid={texture.tid}").content.decode()
        assert "加入衣柜" in html2
        assert "移出衣柜" not in html2
