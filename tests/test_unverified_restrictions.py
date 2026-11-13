"""未验证用户限制测试（管理面板开关控制）。"""

import json

import pytest

from accounts.models import User
from options.models import Option
from players.models import Player

pytestmark = pytest.mark.django_db

BASE = "/api/yggdrasil"


def _unverified(client):
    u = User.objects.create_user(
        email="uv@x.com", nickname="uv", password="password8", verified=False
    )
    Player.objects.create(uid=u, name="Unverified")
    client.force_login(u)
    return client, u


class TestUnverifiedRestrictions:
    def test_skinlib_block(self, client):
        c, _u = _unverified(client)
        Option.objects.set("unverified_block_skinlib", "1")
        assert c.get("/skinlib").status_code == 403
        assert c.get("/skinlib/search?page=1").status_code == 403
        # 详情页同样受限
        assert c.get("/skinlib/show?tid=1").status_code == 403
        # 关闭后恢复
        Option.objects.set("unverified_block_skinlib", "0")
        assert c.get("/skinlib").status_code == 200

    def test_upload_block(self, client):
        c, _u = _unverified(client)
        Option.objects.set("unverified_block_upload", "1")
        assert c.get("/skinlib/upload").status_code == 403
        resp = c.post("/skinlib/upload", {"name": "x", "type": "steve"})
        assert resp.status_code == 403

    def test_verified_user_unaffected(self, client):
        u = User.objects.create_user(
            email="ok@x.com", nickname="ok", password="password8", verified=True
        )
        client.force_login(u)
        Option.objects.set("unverified_block_skinlib", "1")
        Option.objects.set("unverified_block_upload", "1")
        Option.objects.set("unverified_block_import", "1")
        assert client.get("/skinlib").status_code == 200
        assert client.get("/skinlib/upload").status_code == 200

    def test_admin_unaffected(self, client):
        u = User.objects.create_user(
            email="adm@x.com", nickname="adm", password="password8",
            permission=User.Permission.ADMIN, verified=False,
        )
        client.force_login(u)
        Option.objects.set("unverified_block_skinlib", "1")
        assert client.get("/skinlib").status_code == 200  # 管理员豁免

    def test_ygg_block(self, client, db):
        User.objects.create_user(
            email="uy@x.com", nickname="uy", password="password8", verified=False
        )
        Player.objects.create(uid=User.objects.get(email="uy@x.com"), name="UY")
        Option.objects.set("unverified_block_ygg", "1")
        resp = client.post(
            BASE + "/authserver/authenticate",
            data=json.dumps({"username": "uy@x.com", "password": "password8"}),
            content_type="application/json",
        )
        assert resp.status_code == 403
        assert "邮箱未验证" in resp.json()["errorMessage"]

    def test_import_block(self, client):
        c, _u = _unverified(client)
        Option.objects.set("import_enabled", "1")
        Option.objects.set("unverified_block_import", "1")
        resp = c.post("/skinlib/import", {"url": "https://x.com/skinlib/show/1"})
        assert resp.status_code == 403
