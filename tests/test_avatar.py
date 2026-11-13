"""自定义头像功能测试（设为头像 / 头像渲染）。"""

import pytest
from django.core.files.storage import FileSystemStorage
from PIL import Image

from accounts.models import User
from players.models import Player
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


def _png(size=(64, 64), color=(255, 0, 0, 255)):
    import io

    img = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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


def make_texture(owner, ttype="steve", public=True, name="T"):
    from textures import storage as sm

    h = f"{abs(hash(name + ttype)):064x}"[:64]
    sm.texture_storage.save_bytes(h, _png())
    return Texture.objects.create(
        name=name, type=ttype, hash=h, size=1, uploader=owner, public=public
    )


class TestAvatarSubmit:
    def test_set_avatar(self, client, alice):
        t = make_texture(alice)
        client.force_login(alice)
        resp = client.post("/user/avatar", {"sub": "set", "tid": str(t.tid)}).json()
        assert resp["code"] == 0
        alice.refresh_from_db()
        assert alice.avatar == t.tid

    def test_cape_rejected(self, client, alice):
        cape = make_texture(alice, ttype="cape", name="C")
        client.force_login(alice)
        resp = client.post("/user/avatar", {"sub": "set", "tid": str(cape.tid)}).json()
        assert resp["code"] == 1
        assert "皮肤" in resp["message"]

    def test_others_private_texture_rejected(self, client, alice, bob):
        private = make_texture(bob, public=False, name="私")
        client.force_login(alice)
        resp = client.post(
            "/user/avatar", {"sub": "set", "tid": str(private.tid)}
        ).json()
        assert resp["code"] == 1
        assert "无权" in resp["message"]

    def test_unknown_tid_rejected(self, client, alice):
        client.force_login(alice)
        resp = client.post("/user/avatar", {"sub": "set", "tid": "99999"}).json()
        assert resp["code"] == 1

    def test_clear_avatar(self, client, alice):
        t = make_texture(alice)
        alice.avatar = t.tid
        alice.save()
        client.force_login(alice)
        resp = client.post("/user/avatar", {"sub": "clear"}).json()
        assert resp["code"] == 0
        alice.refresh_from_db()
        assert alice.avatar == 0

    def test_anonymous_redirected(self, client, db):
        assert client.post("/user/avatar", {"sub": "clear"}).status_code == 302


class TestMyAvatarRender:
    def _login(self, client, user):
        client.force_login(user)
        return client

    def test_renders_custom_avatar(self, client, alice):
        t = make_texture(alice, name="皮A")
        alice.avatar = t.tid
        alice.save()
        self._login(client, alice)
        resp = client.get("/avatar/me/64.png")
        assert resp.status_code == 200
        img = Image.open(_bytes_io(resp.content))
        assert img.size == (64, 64)
        assert resp["Cache-Control"] == "private, max-age=300"

    def test_falls_back_to_player_skin(self, client, alice):
        # 未设头像但角色有皮肤 → 用角色皮肤
        skin = make_texture(alice, name="角色皮")
        Player.objects.create(uid=alice, name="Hero", tid_skin=skin)
        self._login(client, alice)
        resp = client.get("/avatar/me/128")
        assert resp.status_code == 200
        img = Image.open(_bytes_io(resp.content))
        assert img.size == (128, 128)

    def test_placeholder_when_no_skin(self, client, alice):
        self._login(client, alice)
        resp = client.get("/avatar/me/64.png")
        assert resp.status_code == 302
        assert "/static/icons/user.svg" in resp["Location"]

    def test_invalid_avatar_tid_falls_back(self, client, alice):
        skin = make_texture(alice, name="兜底皮")
        Player.objects.create(uid=alice, name="Hero2", tid_skin=skin)
        alice.avatar = 424242  # 指向不存在的纹理
        alice.save()
        self._login(client, alice)
        resp = client.get("/avatar/me/64.png")
        assert resp.status_code == 200  # 回落角色皮肤

    def test_invalid_size_404(self, client, alice):
        self._login(client, alice)
        assert client.get("/avatar/me/33.png").status_code == 404


class TestShowPageButton:
    def test_skin_page_shows_avatar_btn(self, client, alice):
        t = make_texture(alice)
        client.force_login(alice)
        html = client.get(f"/skinlib/show?tid={t.tid}").content.decode()
        assert 'id="avatar-btn"' in html
        assert "设为头像" in html

    def test_cape_page_no_avatar_btn(self, client, alice):
        cape = make_texture(alice, ttype="cape", name="披")
        client.force_login(alice)
        html = client.get(f"/skinlib/show?tid={cape.tid}").content.decode()
        assert 'id="avatar-btn"' not in html

    def test_current_avatar_state(self, client, alice):
        t = make_texture(alice, name="当前")
        alice.avatar = t.tid
        alice.save()
        client.force_login(alice)
        html = client.get(f"/skinlib/show?tid={t.tid}").content.decode()
        assert "当前头像" in html
        assert "disabled" in html

    def test_navbar_uses_my_avatar_url(self, client, alice):
        client.force_login(alice)
        html = client.get("/").content.decode()
        assert 'src="/avatar/me/64.png"' in html
        assert 'src="/avatar/me/128.png"' in html


def _bytes_io(data):
    import io

    return io.BytesIO(data)
