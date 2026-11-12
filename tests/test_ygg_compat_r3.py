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


# ---- join/hasJoined 客户端 IP 校验 ----


def join_server(client, tokens, server_id="srv", ip="1.2.3.4"):
    return client.post(
        BASE + "/sessionserver/session/minecraft/join",
        data=json.dumps({
            "accessToken": tokens["accessToken"],
            "selectedProfile": tokens["selectedProfile"]["id"],
            "serverId": server_id,
        }),
        content_type="application/json",
        REMOTE_ADDR=ip,
    )


def has_joined(client, server_id="srv", ip=None):
    url = (
        f"{BASE}/sessionserver/session/minecraft/hasJoined"
        f"?username=Steve&serverId={server_id}"
    )
    if ip is not None:
        url += f"&ip={ip}"
    return client.get(url)


class TestJoinIpCheck:
    def test_ip_match_passes(self, client, seeded):
        tokens = authenticate(client)
        assert join_server(client, tokens, ip="1.2.3.4").status_code == 204
        assert has_joined(client, ip="1.2.3.4").status_code == 200

    def test_ip_mismatch_rejected(self, client, seeded):
        tokens = authenticate(client)
        assert join_server(client, tokens, ip="1.2.3.4").status_code == 204
        assert has_joined(client, ip="5.6.7.8").status_code == 204

    def test_ip_omitted_skips_check(self, client, seeded):
        """未提供 ip 参数（prevent-proxy-connections 关闭）不做 IP 校验。"""
        tokens = authenticate(client)
        assert join_server(client, tokens, ip="1.2.3.4").status_code == 204
        assert has_joined(client).status_code == 200

    def test_forwarded_for_used_at_join(self, client, seeded):
        """join 记录的是 X-Forwarded-For 首段（反代后真实客户端 IP）。"""
        tokens = authenticate(client)
        resp = client.post(
            BASE + "/sessionserver/session/minecraft/join",
            data=json.dumps({
                "accessToken": tokens["accessToken"],
                "selectedProfile": tokens["selectedProfile"]["id"],
                "serverId": "srv",
            }),
            content_type="application/json",
            REMOTE_ADDR="10.0.0.1",
            HTTP_X_FORWARDED_FOR="9.9.9.9, 10.0.0.1",
        )
        assert resp.status_code == 204
        assert has_joined(client, ip="9.9.9.9").status_code == 200
        assert has_joined(client, ip="10.0.0.1").status_code == 204
