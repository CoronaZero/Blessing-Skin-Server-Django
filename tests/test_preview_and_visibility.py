"""立绘预览端点 + 皮肤库可见性（对照原版）。"""

import io

import pytest
from PIL import Image

from accounts.models import User
from textures.models import Texture

pytestmark = pytest.mark.django_db


def real_png(size=(64, 32)):
    img = Image.new("RGBA", size, (10, 20, 30, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _storage(tmp_path, settings):
    from django.core.files.storage import FileSystemStorage

    settings.TEXTURE_STORAGE_ROOT = str(tmp_path / "textures")
    from textures import storage as storage_mod

    storage_mod.texture_storage._storage = FileSystemStorage(
        location=settings.TEXTURE_STORAGE_ROOT
    )


@pytest.fixture()
def texture():
    user = User.objects.create_user(email="u@x.com", nickname="u", password="password8")
    t = Texture.objects.create(
        name="T", type="steve", hash="a" * 64, size=1, uploader=user, public=True
    )
    from textures.storage import texture_storage

    texture_storage.save_bytes(t.hash, real_png())
    return t


@pytest.fixture()
def owner_client(client, texture):
    """上传者会话：preview 已登录才可达（与详情页可见性同口径）。"""
    client.force_login(texture.uploader)
    return client


class TestPreviewEndpoint:
    def test_anonymous_redirects_to_login(self, client, texture):
        assert client.get(f"/preview/{texture.tid}?png").status_code == 302

    def test_skin_preview_renders(self, owner_client, texture):
        resp = owner_client.get(f"/preview/{texture.tid}?png")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("image/")
        img = Image.open(io.BytesIO(resp.content))
        # 立绘：双视角 + padding（45° 视角 MC 立绘为瘦高形，比例近似方形）
        assert img.width > 300 and img.height > 300

    def test_cape_preview_renders(self, owner_client, texture):
        texture.type = "cape"
        texture.save()
        resp = owner_client.get(f"/preview/{texture.tid}?png&height=100")
        assert resp.status_code == 200
        img = Image.open(io.BytesIO(resp.content))
        assert img.height == 140

    def test_height_fallback_and_cache_key(self, owner_client, texture):
        """height 非法回落默认不 500；不同 height 独立缓存不互串。"""
        texture.type = "cape"
        texture.save()
        resp = owner_client.get(f"/preview/{texture.tid}?png&height=abc")
        assert resp.status_code == 200
        r_100 = owner_client.get(f"/preview/{texture.tid}?png&height=100").content
        r_300 = owner_client.get(f"/preview/{texture.tid}?png&height=300").content
        assert r_100 != r_300

    def test_unknown_tid_404(self, owner_client):
        assert owner_client.get("/preview/99999?png").status_code == 404

    def test_missing_file_404(self, owner_client, texture):
        from textures.storage import texture_storage

        texture_storage.delete(texture.hash)
        resp = owner_client.get(f"/preview/{texture.tid}?png")
        assert resp.status_code == 404

    def test_cached_second_hit(self, owner_client, texture):
        r1 = owner_client.get(f"/preview/{texture.tid}?png")
        r2 = owner_client.get(f"/preview/{texture.tid}?png")
        assert r1.status_code == r2.status_code == 200
        assert r1.content == r2.content


class TestPreviewVisibility:
    """私密纹理的预览可见性与详情页同口径：仅上传者/管理员。"""

    @pytest.fixture()
    def private_texture(self):
        owner = User.objects.create_user(
            email="o@x.com", nickname="o", password="password8"
        )
        t = Texture.objects.create(
            name="priv", type="steve", hash="5" * 64, size=1,
            uploader=owner, public=False,
        )
        from textures.storage import texture_storage

        texture_storage.save_bytes(t.hash, real_png())
        return t

    def test_stranger_gets_404(self, client, private_texture):
        stranger = User.objects.create_user(
            email="s@x.com", nickname="s", password="password8"
        )
        client.force_login(stranger)
        assert client.get(f"/preview/{private_texture.tid}?png").status_code == 404

    def test_owner_sees_own_private(self, client, private_texture):
        client.force_login(private_texture.uploader)
        assert client.get(f"/preview/{private_texture.tid}?png").status_code == 200

    def test_admin_sees_private(self, client, private_texture):
        admin = User.objects.create_user(
            email="a@x.com", nickname="a", password="password8",
            permission=User.Permission.ADMIN,
        )
        client.force_login(admin)
        assert client.get(f"/preview/{private_texture.tid}?png").status_code == 200


class TestSkinlibVisibility:
    """对照原版 library：admin 全可见；普通用户=公开+自己的；仅此二档。"""

    @pytest.fixture()
    def users(self):
        normal = User.objects.create_user(
            email="n@x.com", nickname="n", password="password8"
        )
        admin = User.objects.create_user(
            email="a@x.com", nickname="a", password="password8",
            permission=User.Permission.ADMIN,
        )
        return normal, admin

    @pytest.fixture()
    def textures(self, users):
        normal, _ = users
        pub = Texture.objects.create(
            name="pub", type="steve", hash="1" * 64, size=1,
            uploader=normal, public=True,
        )
        priv = Texture.objects.create(
            name="priv", type="steve", hash="2" * 64, size=1,
            uploader=normal, public=False,
        )
        return pub, priv

    def test_admin_sees_private_too(self, client, users, textures):
        _, admin = users
        _, priv = textures
        client.force_login(admin)
        resp = client.get("/skinlib/search")
        items = resp.json()["data"]["data"]
        assert any(t["tid"] == priv.tid for t in items), "管理员应看到私密纹理"

    def test_owner_sees_own_private(self, client, users, textures):
        normal, _ = users
        _, priv = textures
        client.force_login(normal)
        items = client.get("/skinlib/search").json()["data"]["data"]
        assert any(t["tid"] == priv.tid for t in items), "用户应看到自己的私密纹理"

    def test_normal_user_hidden_others_private(self, client, users, textures):
        other = User.objects.create_user(
            email="o@x.com", nickname="o", password="password8"
        )
        _, priv = textures
        client.force_login(other)
        items = client.get("/skinlib/search").json()["data"]["data"]
        assert not any(t["tid"] == priv.tid for t in items)

    def test_skin_category_includes_both_models(self, client, users):
        """skin 分类须含 steve+alex（对照原版 whereIn）。"""
        normal, _ = users
        Texture.objects.create(
            name="alexT", type="alex", hash="3" * 64, size=1,
            uploader=normal, public=True,
        )
        Texture.objects.create(
            name="steveT", type="steve", hash="4" * 64, size=1,
            uploader=normal, public=True,
        )
        client.force_login(normal)
        names = [t["name"] for t in
                 client.get("/skinlib/search").json()["data"]["data"]]
        assert "alexT" in names and "steveT" in names
        names = [t["name"] for t in
                 client.get("/skinlib/search?category=alex").json()["data"]["data"]]
        assert "alexT" in names and "steveT" not in names
