"""面板邀请码管理测试（定向）。"""

import pytest

from accounts.models import User
from invitation.models import InvitationCode

pytestmark = pytest.mark.django_db


@pytest.fixture()
def super_client(client):
    User.objects.create_superuser(email="op@x.com", nickname="OP", password="password8")
    client.login(username="op@x.com", password="password8")
    return client


class TestInvitationsPanel:
    def test_page_lists_codes(self, super_client):
        InvitationCode.objects.create()
        html = super_client.get("/admin/invitations").content.decode()
        assert "inv-gen" in html
        assert "可用" in html

    def test_generate_creates_codes(self, super_client):
        assert InvitationCode.objects.count() == 0
        resp = super_client.post("/admin/invitations/generate", {"amount": "3"})
        assert resp.json()["code"] == 0
        assert InvitationCode.objects.count() == 3

    def test_generate_caps_at_100(self, super_client):
        resp = super_client.post("/admin/invitations/generate", {"amount": "500"})
        assert resp.json()["code"] == 0
        assert InvitationCode.objects.count() == 100

    def test_plain_admin_forbidden(self, client):
        User.objects.create_user(
            email="ad@x.com", nickname="ad", password="password8",
            permission=User.Permission.ADMIN,
        )
        client.login(username="ad@x.com", password="password8")
        assert client.get("/admin/invitations").status_code == 404
