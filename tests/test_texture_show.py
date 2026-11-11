"""纹理详情页测试：3D 预览本地 vendor + 私密纹理保护。"""

import pytest

from accounts.models import User
from textures.models import Texture

pytestmark = pytest.mark.django_db


@pytest.fixture()
def owner():
    return User.objects.create_user(email="o@x.com", nickname="owner", password="password8")


@pytest.fixture()
def texture(owner):
    return Texture.objects.create(
        name="测试皮肤", type="steve", hash="a" * 64, size=1,
        uploader=owner, public=True,
    )


class TestTextureShow:
    def test_detail_page_renders_3d_viewer_local(self, client, owner, texture):
        client.force_login(owner)
        resp = client.get(f"/skinlib/show?tid={texture.tid}")
        html = resp.content.decode()
        assert resp.status_code == 200
        # 3D 预览 canvas + 本地 vendor
        assert "viewer-canvas" in html
        assert "/static/vendor/skinview3d.bundle.js" in html
        # 静态资源禁 CDN（含 vendor）
        assert "cdn.jsdelivr" not in html
        assert "unpkg.com" not in html

    def test_alex_uses_slim_model(self, client, owner):
        alex = Texture.objects.create(
            name="A", type="alex", hash="b" * 64, size=1, uploader=owner, public=True
        )
        client.force_login(owner)
        html = client.get(f"/skinlib/show?tid={alex.tid}").content.decode()
        assert 'data-model="slim"' in html

    def test_cape_renders_2d_no_canvas(self, client, owner):
        cape = Texture.objects.create(
            name="C", type="cape", hash="c" * 64, size=1, uploader=owner, public=True
        )
        client.force_login(owner)
        html = client.get(f"/skinlib/show?tid={cape.tid}").content.decode()
        assert "viewer-canvas" not in html
        assert "披风预览" in html

    def test_private_texture_hidden_from_others(self, client, owner):
        private = Texture.objects.create(
            name="P", type="steve", hash="d" * 64, size=1,
            uploader=owner, public=False,
        )
        other = User.objects.create_user(
            email="v@x.com", nickname="v", password="password8"
        )
        client.force_login(other)
        resp = client.get(f"/skinlib/show?tid={private.tid}")
        assert resp.status_code == 404

    def test_private_texture_visible_to_uploader(self, client, owner):
        private = Texture.objects.create(
            name="P2", type="steve", hash="e" * 64, size=1,
            uploader=owner, public=False,
        )
        client.force_login(owner)
        resp = client.get(f"/skinlib/show?tid={private.tid}")
        assert resp.status_code == 200

    def test_closet_and_report_buttons_for_logged_in(self, client, owner, texture):
        client.force_login(owner)
        html = client.get(f"/skinlib/show?tid={texture.tid}").content.decode()
        assert 'id="closet-btn"' in html
        assert 'id="report-btn"' in html
