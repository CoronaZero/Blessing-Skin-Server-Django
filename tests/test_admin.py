"""管理后台测试（自研 panel 已替代自带 admin）。"""

import pytest

from accounts.models import User
from moderation.models import Report
from textures.models import Texture

pytestmark = pytest.mark.django_db


@pytest.fixture()
def super_client(client):
    User.objects.create_superuser(
        email="op@x.com", nickname="OP", password="password8"
    )
    client.login(username="op@x.com", password="password8")
    return client


@pytest.fixture()
def plain_admin_client(client):
    User.objects.create_user(
        email="ad@x.com", nickname="ad", password="password8",
        permission=User.Permission.ADMIN,
    )
    client.login(username="ad@x.com", password="password8")
    return client


class TestPanelAccess:
    @pytest.mark.parametrize("path", [
        "/admin/",
        "/admin/users",
        "/admin/textures",
        "/admin/reports",
        "/admin/options",
    ])
    def test_super_admin_all_pages(self, super_client, path):
        assert super_client.get(path).status_code == 200, path

    def test_plain_admin_no_options(self, plain_admin_client):
        assert plain_admin_client.get("/admin/options").status_code == 404
        assert plain_admin_client.get("/admin/users").status_code == 200

    def test_normal_user_rejected(self, client):
        User.objects.create_user(email="n@x.com", nickname="n", password="x")
        client.force_login(User.objects.get(email="n@x.com"))
        assert client.get("/admin/").status_code == 404


class TestPanelUserActions:
    def test_ban_unban(self, super_client):
        target = User.objects.create_user(email="t@x.com", nickname="t", password="x")
        r = super_client.post(f"/admin/users/{target.uid}/action", {"action": "ban"})
        assert r.json()["code"] == 0
        target.refresh_from_db()
        assert target.permission == User.Permission.BANNED
        r = super_client.post(f"/admin/users/{target.uid}/action", {"action": "unban"})
        assert r.json()["code"] == 0
        target.refresh_from_db()
        assert target.permission == User.Permission.NORMAL

    def test_plain_admin_cannot_change_permission(self, plain_admin_client):
        target = User.objects.create_user(email="t2@x.com", nickname="t2", password="x")
        r = plain_admin_client.post(
            f"/admin/users/{target.uid}/action", {"action": "perm", "level": "2"}
        )
        assert r.json()["code"] != 0

    def test_super_admin_can_promote(self, super_client):
        target = User.objects.create_user(email="t3@x.com", nickname="t3", password="x")
        r = super_client.post(
            f"/admin/users/{target.uid}/action", {"action": "perm", "level": "1"}
        )
        assert r.json()["code"] == 0
        target.refresh_from_db()
        assert target.permission == User.Permission.ADMIN


class TestPanelTextureActions:
    def test_toggle_public_and_delete(self, super_client):
        owner = User.objects.create_user(email="o@x.com", nickname="o", password="x")
        t = Texture.objects.create(
            name="T", type="steve", hash="a" * 64, size=1, uploader=owner, public=True
        )
        r = super_client.post(f"/admin/textures/{t.tid}/action",
                              {"action": "toggle_public"})
        assert r.json()["code"] == 0
        t.refresh_from_db()
        assert t.public is False
        r = super_client.post(f"/admin/textures/{t.tid}/action", {"action": "delete"})
        assert r.json()["code"] == 0
        assert not Texture.objects.filter(tid=t.tid).exists()


class TestPanelReportActions:
    def test_resolve_reject(self, super_client):
        reporter = User.objects.create_user(email="r@x.com", nickname="r", password="x")
        uploader = User.objects.create_user(email="u@x.com", nickname="u", password="x")
        t = Texture.objects.create(
            name="T", type="steve", hash="b" * 64, size=1, uploader=uploader, public=True
        )
        report = Report.objects.create(
            texture=t, uploader=uploader, reporter=reporter, reason="测试"
        )
        r = super_client.post(f"/admin/reports/{report.id}/action",
                              {"action": "resolve"})
        assert r.json()["code"] == 0
        report.refresh_from_db()
        assert report.status == Report.Status.RESOLVED
        r = super_client.post(f"/admin/reports/{report.id}/action",
                              {"action": "reject"})
        assert r.json()["code"] == 0
        report.refresh_from_db()
        assert report.status == Report.Status.REJECTED
