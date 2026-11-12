"""皮肤库详情页材质管理测试（上传者/管理员：公开性切换、重命名、删除）。

对照原版 SkinlibController：privacy 积分差价、重复 hash 检查、
delete 清理佩戴/衣柜/举报引用。
"""

import pytest
from django.core.files.storage import FileSystemStorage

from accounts.models import User
from options.models import Option
from players.models import Player
from textures.models import ClosetItem, Texture

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _tmp_storage(tmp_path, settings):
    settings.TEXTURE_STORAGE_ROOT = str(tmp_path / "t")
    from textures import storage as sm

    sm.texture_storage._storage = FileSystemStorage(
        location=settings.TEXTURE_STORAGE_ROOT
    )
    yield
    sm.texture_storage._storage = None


def make_texture(owner, public=True, hash_value="a" * 64, name="测试材质", size=2):
    from textures import storage as sm

    sm.texture_storage.save_bytes(hash_value, b"x" * 1024)
    return Texture.objects.create(
        name=name, type="steve", hash=hash_value, size=size,
        uploader=owner, public=public,
    )


def login(client, email):
    client.login(username=email, password="password8")
    return client


class TestPermission:
    def test_other_user_rejected(self, client):
        owner = User.objects.create_user(
            email="o@x.com", nickname="o", password="password8", verified=True
        )
        other = User.objects.create_user(
            email="p@x.com", nickname="p", password="password8", verified=True
        )
        t = make_texture(owner)
        login(client, other.email)
        for url in (f"/skinlib/texture/{t.tid}/privacy",
                    f"/skinlib/texture/{t.tid}/name",
                    f"/skinlib/texture/{t.tid}/remove"):
            resp = client.post(url, {"name": "x"})
            assert resp.json()["code"] == 1
            assert "无权" in resp.json()["message"]
        assert Texture.objects.filter(tid=t.tid).exists()

    def test_anonymous_redirected(self, client, db):
        from accounts.models import User as U

        owner = U.objects.create_user(
            email="a@x.com", nickname="a", password="password8"
        )
        t = make_texture(owner)
        assert client.post(
            f"/skinlib/texture/{t.tid}/privacy"
        ).status_code == 302

    def test_admin_allowed_even_not_uploader(self, client):
        owner = User.objects.create_user(
            email="o@x.com", nickname="o", password="password8", verified=True
        )
        admin = User.objects.create_user(
            email="ad@x.com", nickname="ad", password="password8",
            permission=User.Permission.ADMIN, verified=True,
        )
        t = make_texture(owner, name="原名")
        login(client, admin.email)
        resp = client.post(f"/skinlib/texture/{t.tid}/name", {"name": "管理员改名"})
        assert resp.json()["code"] == 0
        t.refresh_from_db()
        assert t.name == "管理员改名"


class TestPrivacyToggle:
    def _setup(self):
        owner = User.objects.create_user(
            email="o1@x.com", nickname="o1", password="password8", verified=True
        )
        return owner

    def test_public_to_private_charges_difference(self, client):
        owner = self._setup()
        Option.objects.set("score_per_storage", "1")
        Option.objects.set("private_score_per_storage", "5")
        Option.objects.set("score_award_per_texture", "10")
        t = make_texture(owner, public=True, size=2)  # 2KB
        owner.score = 100
        owner.save()

        login(client, owner.email)
        resp = client.post(f"/skinlib/texture/{t.tid}/privacy")
        assert resp.json()["code"] == 0

        t.refresh_from_db()
        owner.refresh_from_db()
        assert t.public is False
        # 差价 (5-1)*2 + 奖励回收 10 = 18
        assert owner.score == 100 - 18

    def test_insufficient_score_rejected(self, client):
        owner = self._setup()
        Option.objects.set("score_per_storage", "1")
        Option.objects.set("private_score_per_storage", "50")
        t = make_texture(owner, public=True, size=2)
        owner.score = 10
        owner.save()

        login(client, owner.email)
        resp = client.post(f"/skinlib/texture/{t.tid}/privacy")
        assert resp.json()["code"] == 1
        assert "积分不足" in resp.json()["message"]
        t.refresh_from_db()
        assert t.public is True
        owner.refresh_from_db()
        assert owner.score == 10

    def test_private_to_public_refunds(self, client):
        owner = self._setup()
        Option.objects.set("score_per_storage", "1")
        Option.objects.set("private_score_per_storage", "5")
        t = make_texture(owner, public=False, hash_value="b" * 64, size=2)
        owner.score = 100
        owner.save()
        # 本模型 hash 全局唯一，原版的“转公开撞重复同 hash”分支不可达
        # （已在模型层用 UNIQUE 约束锁定，见 test_textures_models）

        login(client, owner.email)
        resp = client.post(f"/skinlib/texture/{t.tid}/privacy")
        assert resp.json()["code"] == 0
        t.refresh_from_db()
        assert t.public is True
        # 退还差价 (5-1)*2
        owner.refresh_from_db()
        assert owner.score == 100 + 8

    def test_defaults_no_score_change(self, client):
        """未配置计分选项时切换不产生积分变动（默认 0 差价）。"""
        owner = self._setup()
        t = make_texture(owner, public=True, size=3)
        owner.score = 55
        owner.save()

        login(client, owner.email)
        assert client.post(
            f"/skinlib/texture/{t.tid}/privacy"
        ).json()["code"] == 0
        t.refresh_from_db()
        assert t.public is False
        owner.refresh_from_db()
        assert owner.score == 55


class TestRename:
    def test_rename_by_uploader(self, client):
        owner = User.objects.create_user(
            email="r@x.com", nickname="r", password="password8", verified=True
        )
        t = make_texture(owner, name="旧名")
        login(client, owner.email)
        resp = client.post(f"/skinlib/texture/{t.tid}/name", {"name": "新名字"})
        assert resp.json()["code"] == 0
        t.refresh_from_db()
        assert t.name == "新名字"

    def test_empty_name_rejected(self, client):
        owner = User.objects.create_user(
            email="r2@x.com", nickname="r2", password="password8", verified=True
        )
        t = make_texture(owner)
        login(client, owner.email)
        resp = client.post(f"/skinlib/texture/{t.tid}/name", {"name": "   "})
        assert resp.json()["code"] == 1

    def test_overlong_name_truncated_to_50(self, client):
        owner = User.objects.create_user(
            email="r3@x.com", nickname="r3", password="password8", verified=True
        )
        t = make_texture(owner)
        login(client, owner.email)
        resp = client.post(f"/skinlib/texture/{t.tid}/name", {"name": "长" * 60})
        assert resp.json()["code"] == 0
        t.refresh_from_db()
        assert len(t.name) == 50


class TestDelete:
    def _setup(self):
        owner = User.objects.create_user(
            email="d@x.com", nickname="d", password="password8", verified=True
        )
        return owner

    def test_delete_cleans_references_and_file(self, client):
        from textures import storage as sm

        owner = self._setup()
        t = make_texture(owner, hash_value="c" * 64, name="待删")
        player = Player.objects.create(uid=owner, name="Wearer", tid_skin=t)
        ClosetItem.objects.create(owner=owner, texture=t, item_name="x")
        from moderation.models import Report

        Report.objects.create(
            texture=t, uploader=owner, reporter=owner, reason="r"
        )

        login(client, owner.email)
        resp = client.post(f"/skinlib/texture/{t.tid}/remove")
        assert resp.json()["code"] == 0

        assert not Texture.objects.filter(tid=t.tid).exists()
        assert not ClosetItem.objects.filter(texture=t).exists()
        assert not Report.objects.filter(texture=t).exists()
        player.refresh_from_db()
        assert player.tid_skin is None
        assert not sm.texture_storage.exists("c" * 64)  # 唯一 hash → 文件删除

    def test_hash_globally_unique_locked_by_model(self, db):
        """本模型 hash 唯一（迁移零搬动设计），同 hash 两行被 UNIQUE 拒绝。"""
        from django.db import IntegrityError, transaction

        owner = User.objects.create_user(
            email="u@x.com", nickname="u", password="password8"
        )
        make_texture(owner, hash_value="f" * 64)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Texture.objects.create(
                    name="Dup", type="cape", hash="f" * 64, size=1,
                    uploader=owner, public=True,
                )


class TestShowPageRendering:
    def test_manage_buttons_shown_for_uploader(self, client):
        owner = User.objects.create_user(
            email="v@x.com", nickname="v", password="password8", verified=True
        )
        other = User.objects.create_user(
            email="n@x.com", nickname="n", password="password8", verified=True
        )
        t = make_texture(owner, name="看这个")
        login(client, owner.email)
        html = client.get(f"/skinlib/show?tid={t.tid}").content.decode()
        assert "privacy-btn" in html
        assert "rename-btn" in html
        assert "delete-btn" in html

        login(client, other.email)
        html2 = client.get(f"/skinlib/show?tid={t.tid}").content.decode()
        assert "privacy-btn" not in html2
        assert "delete-btn" not in html2

    def test_private_badge_shown(self, client):
        owner = User.objects.create_user(
            email="v2@x.com", nickname="v2", password="password8", verified=True
        )
        t = make_texture(owner, public=False)
        login(client, owner.email)
        html = client.get(f"/skinlib/show?tid={t.tid}").content.decode()
        assert "私密" in html
