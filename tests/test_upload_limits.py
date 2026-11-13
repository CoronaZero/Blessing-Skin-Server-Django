"""站内上传体积/像素上限与渲染像素保护测试（P2-3）。

- max_upload_file_size（KB）：站内上传按原始字节执行（对照 PHP 原版执行项）
- max_texture_width：校验管线像素尺寸上限
- MAX_RENDER_PIXELS：头像/立绘渲染前的像素总量拦截（内存放大 DoS 防护）
"""

import io

import pytest
from django.core.files.storage import FileSystemStorage
from PIL import Image

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _storage(tmp_path, settings):
    settings.TEXTURE_STORAGE_ROOT = str(tmp_path / "t")
    from textures import storage as sm

    sm.texture_storage._storage = FileSystemStorage(
        location=settings.TEXTURE_STORAGE_ROOT
    )


def _png(size=(64, 32)):
    img = Image.new("RGBA", size, (10, 20, 30, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def user_client(client):
    from accounts.models import User

    user = User.objects.create_user(
        email="u@x.com", nickname="u", password="password8"
    )
    client.force_login(user)
    return client


def _upload(client, data, name="t", ttype="steve"):
    import io as _io

    return client.post("/skinlib/upload", {
        "name": name,
        "type": ttype,
        "file": _io.BytesIO(data),
    })


class TestUploadByteLimit:
    def test_oversized_file_rejected_by_option(self, user_client):
        from options.models import Option

        Option.objects.set("max_upload_file_size", "1")
        resp = _upload(user_client, b"x" * 2048)
        assert "体积超过上限" in resp.json()["message"]
        from textures.models import Texture

        assert Texture.objects.count() == 0

    def test_within_limit_accepted(self, user_client):
        from options.models import Option

        Option.objects.set("max_upload_file_size", "64")
        resp = _upload(user_client, _png())
        assert resp.json()["code"] == 0
        # 缺省回落 2048KB，不设选项也不影响常规小图（换像素避开同 hash 去重）
        Option.objects.set("max_upload_file_size", "")
        resp = _upload(user_client, _png((128, 64)), name="t2")
        assert resp.json()["code"] == 0


class TestPixelLimitOption:
    def test_width_over_option_rejected(self, user_client):
        from options.models import Option

        Option.objects.set("max_texture_width", "64")
        resp = _upload(user_client, _png((128, 64)))  # 比例合法但宽度超限
        assert "尺寸超过上限" in resp.json()["message"]

    def test_within_option_accepted(self, user_client):
        from options.models import Option

        Option.objects.set("max_texture_width", "256")
        resp = _upload(user_client, _png((128, 64)))
        assert resp.json()["code"] == 0


class TestRenderPixelCap:
    @pytest.fixture()
    def worn_player(self):
        from accounts.models import User
        from players.models import Player
        from textures.models import Texture
        from textures.storage import texture_storage

        u = User.objects.create_user(
            email="w@x.com", nickname="w", password="password8"
        )
        t = Texture.objects.create(
            name="T", type="steve", hash="e" * 64, size=1, uploader=u, public=True
        )
        texture_storage.save_bytes(t.hash, _png((64, 32)))
        return Player.objects.create(uid=u, name="WornMan", tid_skin=t)

    def test_avatar_render_blocked_over_cap(self, client, worn_player, monkeypatch):
        """像素总量超上限：未鉴权头像端点不得全量解码（404 兜底）。"""
        from textures import rendering

        assert client.get("/avatar/64/WornMan.png").status_code == 200
        monkeypatch.setattr(rendering, "MAX_RENDER_PIXELS", 1024)  # 64x32=2048
        assert client.get("/avatar/64/WornMan.png").status_code == 404

    def test_preview_render_blocked_over_cap(self, client, worn_player, monkeypatch):
        from textures import rendering

        client.force_login(worn_player.uid)
        monkeypatch.setattr(rendering, "MAX_RENDER_PIXELS", 1024)
        assert client.get(f"/preview/{worn_player.tid_skin.tid}?png").status_code == 404
