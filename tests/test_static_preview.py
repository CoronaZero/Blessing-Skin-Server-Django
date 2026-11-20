"""皮肤库 2D 预览伪静态端点测试（CDN 长缓存）。"""

import io

import pytest
from django.core.files.storage import FileSystemStorage
from PIL import Image

from accounts.models import User
from textures.models import Texture

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


@pytest.fixture()
def viewer(client):
    u = User.objects.create_user(
        email="v@x.com", nickname="v", password="password8", verified=True
    )
    client.force_login(u)
    return client


def _make_texture(owner, *, public=True, name="S"):
    img = Image.new("RGBA", (64, 64), (200, 30, 30, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    import hashlib

    h = hashlib.sha256(data).hexdigest()
    from textures import storage as sm

    sm.texture_storage.save_bytes(h, data)
    return Texture.objects.create(
        name=name, type="steve", hash=h, size=1, uploader=owner, public=public
    )


class TestStaticPreviewEndpoint:
    def test_public_texture_served_with_immutable_cache(self, viewer):
        owner = User.objects.create_user(
            email="o@x.com", nickname="o", password="password8"
        )
        t = _make_texture(owner)
        url = f"/preview/{t.tid}-{t.hash[:8]}-h150.png"
        resp = viewer.get(url)
        assert resp.status_code == 200
        # CDN 一年长缓存 + immutable（指纹 URL：内容变则 hash 变）
        cc = resp["Cache-Control"]
        assert "max-age=31536000" in cc and "immutable" in cc
        assert resp["Content-Type"].startswith("image/")

    def test_hash_mismatch_404(self, viewer):
        """hash8 指纹不符（皮肤已更换后访问旧 URL）→ 404，防 CDN 旧 URL 污染。"""
        owner = User.objects.create_user(
            email="o2@x.com", nickname="o2", password="password8"
        )
        t = _make_texture(owner)
        resp = viewer.get(f"/preview/{t.tid}-{'f' * 8}-h150.png")
        assert resp.status_code == 404

    def test_private_texture_rejected(self, viewer):
        """私密纹理不走伪静态端点（CDN 不应缓存非公开内容）。"""
        owner = User.objects.create_user(
            email="o3@x.com", nickname="o3", password="password8"
        )
        t = _make_texture(owner, public=False)
        resp = viewer.get(f"/preview/{t.tid}-{t.hash[:8]}-h150.png")
        assert resp.status_code == 404

    def test_height_clamped_and_cached_same(self, viewer):
        owner = User.objects.create_user(
            email="o4@x.com", nickname="o4", password="password8"
        )
        t = _make_texture(owner)
        ok = viewer.get(f"/preview/{t.tid}-{t.hash[:8]}-h150.png")
        assert ok.status_code == 200
        # 越界高度钳制（>1024 → 1024）不报错
        big = viewer.get(f"/preview/{t.tid}-{t.hash[:8]}-h5000.png")
        assert big.status_code == 200
