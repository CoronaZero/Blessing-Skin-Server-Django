"""Yggdrasil P2/P3 协议对齐回归（定向）。

覆盖审查清单中 P2/P3 级修复项，每项配锁定测试：
- P2-26: setup 生成签名密钥 4096 位
- P2-21: 披风 22x17 遗留尺寸（透明补足至 64x32 倍数）
- P3-31: accessToken JWT 补 spr claim
- P3-30: 日志写库受 YGG_VERBOSE_LOG 开关控制
- P3-34: 令牌 access_token 唯一约束
- P3-32: authenticate 时清理过期令牌行
- P2-24: 面板 Yggdrasil 日志页
- P2-23: 面板私钥编辑/重新生成
- P2-25: 上传材质计分/衣柜/命名（对照 ProfileController.php）
- P2-20: 材质 hash 像素级 SHA-256（规范 L243-254）
"""

import json

import pytest

from accounts.models import User
from players.models import Player

pytestmark = pytest.mark.django_db

BASE = "/api/yggdrasil"


@pytest.fixture()
def seeded(db):
    user = User.objects.create_user(
        email="y@x.com", nickname="y", password="password8", verified=True
    )
    Player.objects.create(uid=user, name="Steve")
    return user


def authenticate(client, **extra):
    resp = client.post(
        BASE + "/authserver/authenticate",
        data=json.dumps({"username": "y@x.com", "password": "password8", **extra}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    return resp.json()


# ---- P2-26: setup 密钥 4096 位 ----


class TestP2SetupKeySize:
    def test_setup_generates_4096_bit_key(self, db):
        from setup.views import _generate_ygg_keypair

        _generate_ygg_keypair()
        from cryptography.hazmat.primitives import serialization

        from options.models import Option

        pem = Option.objects.get_value("ygg_private_key")
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        assert key.key_size == 4096
