"""纹理 HTTP 端点测试（TDD 先行）。

对照原版路由（routes/web.php + static.php）：
- GET  /skinlib/search?q=           皮肤库列表（JSON）
- POST /skinlib/upload              上传
- GET  /textures/{hash}            纹理文件（对照原版 JS 版 avatar/previews）
- GET  /avatar/{size}/{player}     CustomSkinAPI 头像
- GET  /{player}.json              CustomSkinAPI R1 profile
- 上传语义：去重（public 或本人）→ 计分 → 存储
"""

import io
import json  # noqa: F401

import pytest
from PIL import Image

from accounts.models import User
from players.models import Player
from textures.models import Texture

pytestmark = pytest.mark.django_db


def png_bytes(size, color=(255, 0, 0, 255)) -> bytes:
    img = Image.new("RGBA", size, color)
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
def user():
    return User.objects.create_user(
        email="u@x.com", nickname="u", password="password8", score=100
    )


@pytest.fixture()
def auth_client(client, user):
    client.force_login(user)
    return client


SKIN = png_bytes((64, 32))


class TestSearchApi:
    def test_requires_auth(self, client):
        resp = client.get("/skinlib/search")
        assert resp.status_code in (302, 401)

    def test_returns_json_list(self, auth_client, user):
        Texture.objects.create(
            name="皮肤1", type="steve", hash="a" * 64, size=1, uploader=user, public=True
        )
        resp = auth_client.get("/skinlib/search")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["code"] == 0
        items = payload["data"]["data"]  # Laravel 分页嵌套
        assert len(items) == 1
        assert items[0]["name"] == "皮肤1"

    def test_filter_by_keyword(self, auth_client, user):
        Texture.objects.create(
            name="Steve Red", type="steve", hash="a" * 64, size=1,
            uploader=user, public=True,
        )
        Texture.objects.create(
            name="Cape Blue", type="cape", hash="b" * 64, size=1,
            uploader=user, public=True,
        )
        resp = auth_client.get("/skinlib/search?keyword=steve")
        items = resp.json()["data"]["data"]
        assert len(items) == 1
        assert items[0]["name"] == "Steve Red"

    def test_private_texture_hidden_from_others(self, client, user):
        Texture.objects.create(
            name="私密", type="steve", hash="c" * 64, size=1,
            uploader=user, public=False,
        )
        other = User.objects.create_user(email="v@x.com", nickname="v", password="password8")
        client.force_login(other)
        resp = client.get("/skinlib/search")
        assert resp.json()["data"]["data"] == []

    def test_sort_options(self, auth_client, user):
        for i, likes in enumerate([5, 10]):
            Texture.objects.create(
                name=f"t{i}", type="steve", hash=str(i) * 64, size=1,
                uploader=user, public=True, likes=likes,
            )
        resp = auth_client.get("/skinlib/search?sort=likes")
        items = resp.json()["data"]["data"]
        assert items[0]["likes"] >= items[1]["likes"]


class TestUploadApi:
    def _upload(self, client, name="皮肤", filedata=SKIN, type_="steve", **extra):
        return client.post(
            "/skinlib/upload",
            data={
                "name": name,
                "type": type_,
                "public": "1",
                "file": io.BytesIO(filedata),
                **extra,
            },
            format="multipart",
        )

    def test_upload_success(self, auth_client, user):
        resp = self._upload(auth_client)
        assert resp.json()["code"] == 0, resp.json()
        t = Texture.objects.first()
        assert t is not None
        assert t.uploader == user
        assert t.public is True
        # 文件已按 hash 落盘
        from textures.storage import texture_storage as storage

        assert storage.exists(t.hash)

    def test_upload_dedup_by_hash(self, auth_client, user):
        r1 = self._upload(auth_client, name="first")
        r2 = self._upload(auth_client, name="second")
        assert r1.json()["code"] == 0
        # 重复上传：返回 code=2 + 已有 tid（对照原版 repeated）
        assert r2.json()["code"] == 2
        assert "tid" in r2.json()["data"]
        assert Texture.objects.count() == 1

    def test_upload_invalid_size(self, auth_client):
        resp = self._upload(auth_client, filedata=png_bytes((100, 100)))
        assert resp.json()["code"] == 1

    def test_upload_requires_auth(self, client):
        resp = self._upload(client)
        assert resp.status_code in (302, 401)

    def test_upload_score_deduction(self, auth_client, user):
        """上传扣积分：score_per_storage * KB + score_per_closet_item。"""
        from options.models import Option

        Option.objects.set("score_per_storage", "1")
        Option.objects.set("score_per_closet_item", "1")
        Option.objects.set("score_award_per_texture", "0")
        before = user.score
        resp = self._upload(auth_client)
        assert resp.json()["code"] == 0
        user.refresh_from_db()
        assert user.score < before  # 扣分

    def test_upload_insufficient_score(self, auth_client, user):
        from options.models import Option

        Option.objects.set("score_per_storage", "10000")
        user.score = 0
        user.save()
        resp = self._upload(auth_client)
        assert resp.json()["code"] == 1
        assert "积分" in resp.json()["message"]


class TestTextureFileEndpoint:
    def test_serve_texture(self, client, user):
        t = Texture.objects.create(
            name="s", type="steve", hash="e" * 64, size=1,
            uploader=user, public=True,
        )
        from textures.storage import texture_storage

        texture_storage.save_bytes(t.hash, SKIN)
        resp = client.get(f"/textures/{t.hash}")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"
        assert resp["ETag"] == f'"{t.hash}"'

    def test_304_on_match(self, client, user):
        t = Texture.objects.create(
            name="s", type="steve", hash="f" * 64, size=1,
            uploader=user, public=True,
        )
        from textures.storage import texture_storage

        texture_storage.save_bytes(t.hash, SKIN)
        resp = client.get(f"/textures/{t.hash}", HTTP_IF_NONE_MATCH=f'"{t.hash}"')
        assert resp.status_code == 304

    def test_invalid_hash_404(self, client):
        resp = client.get("/textures/not-a-hash")
        assert resp.status_code == 404

    def test_missing_file_404(self, client, user):
        Texture.objects.create(
            name="s", type="steve", hash="9" * 64, size=1,
            uploader=user, public=True,
        )
        resp = client.get("/textures/" + "9" * 64)
        assert resp.status_code == 404


class TestAvatarEndpoint:
    def test_avatar_rendered(self, client, user):
        player = Player.objects.create(uid=user, name="Steve")
        t = Texture.objects.create(
            name="s", type="steve", hash="d" * 64, size=1,
            uploader=user, public=True,
        )
        player.tid_skin = t
        player.save()
        from textures.storage import texture_storage

        texture_storage.save_bytes(t.hash, SKIN)
        resp = client.get(f"/avatar/64/{player.name}.png")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"

    def test_avatar_size_variants_map(self, client, user):
        player = Player.objects.create(uid=user, name="Alex")
        t = Texture.objects.create(
            name="s", type="steve", hash="1" * 64, size=1,
            uploader=user, public=True,
        )
        player.tid_skin = t
        player.save()
        from textures.storage import texture_storage

        texture_storage.save_bytes(t.hash, SKIN)
        assert client.get("/avatar/32/Alex.png").status_code == 200
        assert client.get("/avatar/250/Alex.png").status_code == 404  # 非法尺寸

    def test_unknown_player_fallback(self, client):
        resp = client.get("/avatar/64/Ghost.png")
        # 未配置默认皮肤时 404（对照原版 204 option）
        assert resp.status_code in (404, 204)


class TestPlayerJsonEndpoint:
    def test_custom_skin_api_r1(self, client, user):
        player = Player.objects.create(uid=user, name="Steve")
        t = Texture.objects.create(
            name="s", type="steve", hash="2" * 64, size=1,
            uploader=user, public=True,
        )
        player.tid_skin = t
        player.save()
        resp = client.get("/Steve.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "Steve"
        assert data["skins"]["default"] == "2" * 64
        assert data["cape"] is None

    def test_unknown_player_404(self, client):
        resp = client.get("/Ghost.json")
        assert resp.status_code == 404
