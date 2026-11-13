"""panel 正版角色绑定管理页测试：权限 / 列表搜索 / 解绑与冷却。"""

import pytest

from accounts.models import User
from oauth_bridge.models import MojangBinding, UnbindCooldown

pytestmark = pytest.mark.django_db

URL = "/admin/mojang-bindings"


def make(email, permission=None):
    kwargs = {"email": email, "nickname": email.split("@")[0],
              "password": "password8", "verified": True}
    if permission is not None:
        kwargs["permission"] = permission
    return User.objects.create_user(**kwargs)


@pytest.fixture()
def admin():
    return make("adm@x.com", User.Permission.ADMIN)


@pytest.fixture()
def super_admin():
    return make("root@x.com", User.Permission.SUPER_ADMIN)


@pytest.fixture()
def binding():
    user = make("u@x.com")
    return MojangBinding.objects.create(
        user=user, mc_uuid="a" * 32, mc_name="Notch"
    )


class TestAccess:
    def test_anonymous_redirected_to_login(self, client):
        assert client.get(URL).status_code == 302

    def test_normal_user_gets_404(self, client):
        client.force_login(make("p@x.com"))
        assert client.get(URL).status_code == 404

    def test_admin_ok(self, client, admin, binding):
        client.force_login(admin)
        resp = client.get(URL)
        assert resp.status_code == 200
        assert b"Notch" in resp.content

    def test_super_admin_ok(self, client, super_admin, binding):
        client.force_login(super_admin)
        assert client.get(URL).status_code == 200

    def test_nav_entry_visible_for_admin(self, client, admin):
        client.force_login(admin)
        resp = client.get("/admin/")
        assert b"mojang-bindings" in resp.content


class TestListAndSearch:
    def test_shows_uuid_and_owner(self, client, admin, binding):
        client.force_login(admin)
        resp = client.get(URL)
        assert (binding.mc_uuid).encode() in resp.content
        assert b"u@x.com" in resp.content
        # 只读说明
        assert "只读".encode() in resp.content

    def test_search_by_mc_name(self, client, admin, binding):
        other = make("o@x.com")
        MojangBinding.objects.create(user=other, mc_uuid="b" * 32, mc_name="Jeb")
        client.force_login(admin)
        resp = client.get(URL, {"q": "Notch"})
        assert b"Notch" in resp.content
        assert b"Jeb" not in resp.content

    def test_search_by_owner_email(self, client, admin, binding):
        client.force_login(admin)
        resp = client.get(URL, {"q": "u@x.com"})
        assert b"Notch" in resp.content
        # 无关用户不出现
        resp = client.get(URL, {"q": "zzz"})
        assert b"Notch" not in resp.content


class TestUnbind:
    def test_unbind_success(self, client, admin, binding):
        client.force_login(admin)
        resp = client.post(f"{URL}/{binding.id}/unbind", {"action": "unbind"})
        assert resp.json()["code"] == 0
        assert not MojangBinding.objects.filter(id=binding.id).exists()
        # 管理员解绑同样记冷却（与用户自助解绑规则一致）
        assert UnbindCooldown.objects.filter(
            user=binding.user_id, kind="genuine"
        ).exists()

    def test_unbind_missing_row(self, client, admin):
        client.force_login(admin)
        resp = client.post(f"{URL}/9999/unbind", {"action": "unbind"})
        assert resp.json()["code"] != 0

    def test_unbind_requires_admin(self, client, binding):
        client.force_login(make("p@x.com"))
        resp = client.post(f"{URL}/{binding.id}/unbind", {"action": "unbind"})
        assert resp.status_code == 404
        assert MojangBinding.objects.filter(id=binding.id).exists()
