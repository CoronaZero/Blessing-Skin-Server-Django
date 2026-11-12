"""Yggdrasil 兼容性第三轮修复回归（规范全文 + authlib-injector 源码核对）。

- 全站 ALI 头（裸域名发现，对照原版 AddApiIndicationHeader）
- join/hasJoined 客户端 IP 校验（原版 SessionController）
- v3 算法下改名重算角色 UUID（原版 uuid 表重映射）
- skinDomains 小写归一并剥离端口（客户端 URI.getHost 匹配语义）
- clientToken 长度放宽（规范 L408：任意字符串）
- publickeys 响应对齐原版结构
- 像素 hash 规范官方测试向量（L360-377）
"""

import json

import pytest

from accounts.models import User
from options.models import Option
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


# ---- 全站 ALI 头（裸域名发现）----

ALI_HEADER = "X-Authlib-Injector-API-Location"


class TestAliSiteWide:
    def test_homepage_carries_ali(self, client, db):
        """启动器只填域名时抓的是首页，ALI 必须在那里出现。"""
        resp = client.get("/")
        assert resp[ALI_HEADER] == "/api/yggdrasil/"

    def test_api_root_carries_ali(self, client, db):
        resp = client.get(BASE + "/")
        assert resp[ALI_HEADER] == "/api/yggdrasil/"

    def test_disabled_option_removes_header(self, client, db):
        Option.objects.set("ygg_enable_ali", "0")
        assert ALI_HEADER not in client.get("/").headers
        assert ALI_HEADER not in client.get(BASE + "/").headers
