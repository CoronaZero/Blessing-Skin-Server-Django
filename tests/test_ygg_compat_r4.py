"""Yggdrasil 兼容性第四轮修复回归（静态审查报告核实修复）。

- P0-1: refresh 选角后 join（profile_id 漏存修复）
- P0-2: hello 无密钥时惰性生成 4096 密钥对（禁止空串 signaturePublickey）
- P1-3/P2-5: 双属性签名 + signatureRequired 位于 textures JSON 内
- P1-4: 材质 URL base 用 site_url 优先
- P2-9: refresh 选角不属于该用户返回 403
- P3 杂项：超长 clientToken、search_profiles 元素类型等
"""

import io
import json

import pytest
from PIL import Image

from accounts.models import User
from players.models import Player

pytestmark = pytest.mark.django_db

BASE = "/api/yggdrasil"


def png_bytes(size, color=(255, 0, 0, 255)) -> bytes:
    img = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def api(client, method, path, payload=None, **kwargs):
    fn = getattr(client, method)
    if payload is not None:
        kwargs.setdefault("content_type", "application/json")
        kwargs["data"] = json.dumps(payload)
    return fn(BASE + path, **kwargs)


@pytest.fixture()
def multi_profile_user(db):
    user = User.objects.create_user(
        email="m@x.com", nickname="m", password="password8", verified=True
    )
    Player.objects.create(uid=user, name="Alpha")
    Player.objects.create(uid=user, name="Beta")
    return user


def auth(client, username="m@x.com", **extra):
    payload = {"username": username, "password": "password8", **extra}
    resp = api(client, "post", "/authserver/authenticate", payload)
    assert resp.status_code == 200
    return resp.json()


def join(client, tokens, profile_id, server_id="srv-x"):
    resp = api(client, "post", "/sessionserver/session/minecraft/join", {
        "accessToken": tokens["accessToken"],
        "selectedProfile": profile_id,
        "serverId": server_id,
    })
    return resp


# ---- P0-1: refresh 选角后 join ----


class TestP0SelectProfileFlow:
    def test_refresh_select_then_join(self, client, multi_profile_user):
        alpha = Player.objects.get(name="Alpha")

        # 多角色：authenticate 不预绑定
        tokens = auth(client)
        assert "selectedProfile" not in tokens
        # 直接 join 未选角 → 403
        assert join(client, tokens, alpha.uuid).status_code == 403

        # refresh 选角 Alpha
        resp = api(client, "post", "/authserver/refresh", {
            "accessToken": tokens["accessToken"],
            "clientToken": tokens["clientToken"],
            "selectedProfile": {"id": alpha.uuid, "name": "Alpha"},
        })
        assert resp.status_code == 200
        refreshed = resp.json()
        assert refreshed["selectedProfile"]["id"] == alpha.uuid

        # join 用新令牌（refresh 后旧令牌已轮换）
        resp2 = join(client, refreshed, alpha.uuid)
        assert resp2.status_code == 204, resp2.content.decode()[:200]

        # DB 持久化验证（不止内存 JWT spr）
        from yggdrasil.models import YggdrasilToken

        row = YggdrasilToken.objects.get(access_token=refreshed["accessToken"])
        assert row.profile_id == alpha.uuid