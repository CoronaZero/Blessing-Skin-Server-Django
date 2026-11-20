"""likes 收藏计数维护测试（对照原版语义：衣柜增删 ±1，上传即自收藏）。"""

import pytest

from accounts.models import User
from textures.models import ClosetItem, Texture

pytestmark = pytest.mark.django_db


@pytest.fixture()
def owner():
    return User.objects.create_user(
        email="o@x.com", nickname="o", password="password8", verified=True
    )


@pytest.fixture()
def texture(owner):
    return Texture.objects.create(
        name="T", type="steve", hash="7" * 64, size=1, uploader=owner, public=True
    )


def _client(client, email):
    client.login(username=email, password="password8")
    return client


class TestLikesMaintenance:
    def test_closet_add_increments_likes(self, client, texture, owner):
        c = _client(client, "o@x.com")
        resp = c.post("/user/closet", {"tid": str(texture.tid)})
        assert resp.json()["code"] == 0
        texture.refresh_from_db()
        assert texture.likes == 1  # 加入即收藏 +1

    def test_closet_remove_decrements_likes(self, client, texture, owner):
        c = _client(client, "o@x.com")
        c.post("/user/closet", {"tid": str(texture.tid)})
        item = ClosetItem.objects.get(owner=owner)
        resp = c.post(f"/user/closet/{item.id}/remove")
        assert resp.json()["code"] == 0
        texture.refresh_from_db()
        assert texture.likes == 0

    def test_remove_never_negative(self, client, texture, owner):
        """计数异常漂移（历史数据）时移除不产生负数。"""
        texture.likes = 0
        texture.save(update_fields=["likes"])
        c = _client(client, "o@x.com")
        c.post("/user/closet", {"tid": str(texture.tid)})
        texture.likes = 0  # 人为漂移
        texture.save(update_fields=["likes"])
        item = ClosetItem.objects.get(owner=owner)
        c.post(f"/user/closet/{item.id}/remove")
        texture.refresh_from_db()
        assert texture.likes == 0

    def test_multiple_users_accumulate(self, client, texture, owner):
        User.objects.create_user(
            email="b@x.com", nickname="b", password="password8", verified=True
        )
        c = _client(client, "o@x.com")
        c.post("/user/closet", {"tid": str(texture.tid)})
        c2 = _client(client, "b@x.com")
        c2.post("/user/closet", {"tid": str(texture.tid)})
        texture.refresh_from_db()
        assert texture.likes == 2

    def test_upload_sets_likes_and_creates_closet_item(
        self, client, owner, tmp_path, settings
    ):
        """站内上传：likes=1 且自动入衣柜（对照原版 attach；费用已含衣柜项）。"""
        import io

        from django.core.files.storage import FileSystemStorage
        from PIL import Image

        settings.TEXTURE_STORAGE_ROOT = str(tmp_path / "t")
        from textures import storage as sm

        sm.texture_storage._storage = FileSystemStorage(
            location=settings.TEXTURE_STORAGE_ROOT
        )
        img = Image.new("RGBA", (64, 64), (10, 200, 30, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        client.force_login(owner)
        resp = client.post(
            "/skinlib/upload",
            data={
                "name": "我的皮肤",
                "type": "steve",
                "public": "1",
                "file": io.BytesIO(buf.getvalue()),
            },
            format="multipart",
        )
        assert resp.json()["code"] == 0, resp.json()
        t = Texture.objects.get(tid=resp.json()["data"]["tid"])
        assert t.likes == 1  # 上传即自收藏
        assert ClosetItem.objects.filter(owner=owner, texture=t).exists()
        # 文件 hash 校验（storage 落盘）
        assert sm.texture_storage.exists(t.hash)
