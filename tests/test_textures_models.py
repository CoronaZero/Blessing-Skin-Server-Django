"""textures 模型测试（TDD 先行）。

对照 PHP 原版 Texture 与 user_closet：
- textures：tid/name/type(steve|alex|cape)/hash(sha256)/size/uploader/public/upload_at/likes
- 文件存储以 hash 为名，磁盘零搬动迁移
- user_closet（衣柜）：user ↔ texture 多对多 + item_name
- texture-description 内嵌能力 → description 字段
"""

import pytest
from django.core.exceptions import ValidationError

from accounts.models import User
from players.models import Player
from textures.models import ClosetItem, Texture


def make_user(email="u@x.com") -> User:
    return User.objects.create_user(email=email, nickname=email.split("@")[0], password="x")


def make_skin(owner, name="skin", type_="steve", hash_=None, **kwargs) -> Texture:
    return Texture.objects.create(
        name=name,
        type=type_,
        hash=hash_ or "a" * 64,
        size=1,
        uploader=owner,
        public=True,
        **kwargs,
    )


class TestTextureModel:
    def test_create_texture_fields(self, db):
        owner = make_user()
        t = make_skin(owner, name="我的皮肤", hash_="f" * 64)
        assert t.tid is not None
        assert t.type == "steve"
        assert t.hash == "f" * 64
        assert t.size == 1
        assert t.uploader == owner
        assert t.public is True
        assert t.upload_at is not None

    def test_hash_unique(self, db):
        owner = make_user()
        make_skin(owner, hash_="1" * 64)
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            make_skin(owner, hash_="1" * 64)

    def test_type_choices_validation(self, db):
        owner = make_user()
        t = make_skin(owner, type_="weird")
        with pytest.raises(ValidationError):
            t.full_clean()

    def test_type_choices_valid(self, db):
        owner = make_user()
        for type_ in ("steve", "alex", "cape"):
            t = make_skin(owner, type_=type_, hash_=type_[0] * 64)
            t.full_clean()  # 不抛异常

    def test_model_property(self, db):
        owner = make_user()
        assert make_skin(owner, type_="alex", hash_="a" * 64).model == "slim"
        assert make_skin(owner, hash_="b" * 64).model == "default"
        assert make_skin(owner, type_="cape", hash_="c" * 64).model == "default"

    def test_owner_relation(self, db):
        owner = make_user()
        t = make_skin(owner, hash_="d" * 64)
        assert t.uploader.email == "u@x.com"
        assert owner.uploads.count() == 1

    def test_description_field_default_empty(self, db):
        """texture-description 内嵌能力：描述字段。"""
        owner = make_user()
        t = make_skin(owner, hash_="e" * 64)
        assert t.description == ""


class TestCloset:
    def test_user_closet_add_remove(self, db):
        owner = make_user()
        t = make_skin(owner, hash_="2" * 64)
        ClosetItem.objects.create(owner=owner, texture=t, item_name="我的收藏")
        assert owner.closet.count() == 1
        item = owner.closet.first()
        assert item.item_name == "我的收藏"
        assert item.texture == t

    def test_closet_unique_per_user(self, db):
        owner = make_user()
        t = make_skin(owner, hash_="3" * 64)
        ClosetItem.objects.create(owner=owner, texture=t)
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            ClosetItem.objects.create(owner=owner, texture=t)

    def test_closet_shared_between_users(self, db):
        a, b = make_user("a@x.com"), make_user("b@x.com")
        t = make_skin(a, hash_="4" * 64)
        ClosetItem.objects.create(owner=a, texture=t)
        ClosetItem.objects.create(owner=b, texture=t)
        assert a.closet.count() == 1
        assert b.closet.count() == 1

    def test_likers_backref(self, db):
        a, b = make_user("a@x.com"), make_user("b@x.com")
        t = make_skin(a, hash_="5" * 64)
        ClosetItem.objects.create(owner=b, texture=t, item_name="赞的皮肤")
        likers = set(t.likers.all())
        assert likers == {b}

    def test_add_timestamp(self, db):
        """schema 重设计：衣柜项记录加入时间（原表无此列）。"""
        owner = make_user()
        t = make_skin(owner, hash_="6" * 64)
        item = ClosetItem.objects.create(owner=owner, texture=t)
        assert item.added_at is not None


class TestPlayerTextureIntegration:
    def test_cascade_on_texture_delete_keeps_player(self, db):
        """纹理删除 → 角色引用置空（SET_NULL），角色不删。"""
        owner = make_user()
        t = make_skin(owner, hash_="7" * 64)
        player = Player.objects.create(uid=owner, name="Test1", tid_skin=t)
        t.delete()
        player.refresh_from_db()
        assert player.tid_skin is None
        assert player.model == "default"

    def test_uploads_relation_name(self, db):
        owner = make_user()
        make_skin(owner, hash_="8" * 64)
        make_skin(owner, hash_="9" * 64, type_="cape")
        assert owner.uploads.count() == 2
