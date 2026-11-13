"""Minecraft 好友系统协议端点测试。"""

import json

import pytest
from django.test import Client

from accounts.models import User
from options.models import Option
from players.models import Friendship, Player

pytestmark = pytest.mark.django_db

BASE = "/api/yggdrasil"
MS = BASE + "/minecraftservices"


def auth(client, email="a@x.com", password="password8"):
    resp = client.post(
        BASE + "/authserver/authenticate",
        data=json.dumps({"username": email, "password": password}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content.decode()[:200]
    return resp.json()


def api(client, method, path, payload=None, token=None):
    kwargs = {"content_type": "application/json"}
    if payload is not None:
        kwargs["data"] = json.dumps(payload)
    if token:
        kwargs["headers"] = {"Authorization": f"Bearer {token}"}
    return getattr(client, method)(path, **kwargs)


@pytest.fixture()
def alice(db):
    u = User.objects.create_user(
        email="a@x.com", nickname="alice", password="password8", verified=True
    )
    p = Player.objects.create(uid=u, name="Alice")
    return u, p


@pytest.fixture()
def bob(db):
    u = User.objects.create_user(
        email="b@x.com", nickname="bob", password="password8", verified=True
    )
    p = Player.objects.create(uid=u, name="Bob")
    return u, p


# ---- 鉴权 ----


class TestFriendsAuth:
    def test_no_token_401(self, client, db):
        assert client.get(MS + "/friends").status_code == 401

    def test_unbound_profile_403(self, client, db):
        User.objects.create_user(
            email="c@x.com", nickname="c", password="password8", verified=True
        )
        # 无角色 → authenticate 无 selectedProfile；token 未绑定 profile
        tokens = auth(client, "c@x.com")
        assert "selectedProfile" not in tokens
        resp = client.get(
            MS + "/friends",
            headers={"Authorization": f"Bearer {tokens['accessToken']}"},
        )
        assert resp.status_code == 403

    def test_error_body_shape(self, client, db):
        resp = client.get(MS + "/friends")
        data = resp.json()
        assert data["error"] == "UNAUTHORIZED"
        assert "path" in data and "errorMessage" in data


# ---- GET /friends 快照 ----


class TestFriendsList:
    def test_empty_snapshot(self, client, alice):
        tokens = auth(client)
        resp = api(client, "get", MS + "/friends", token=tokens["accessToken"])
        assert resp.status_code == 200
        assert resp.json() == {
            "friends": [], "incomingRequests": [], "outgoingRequests": [],
        }

    def test_snapshot_with_states(self, client, alice, bob):
        ua, pa = alice
        ub, pb = bob
        tokens = auth(client)
        # 构造：Alice→Bob outgoing；Carol→Alice incoming；Dave <-> Alice friends
        carol = User.objects.create_user(
            email="c9@x.com", nickname="c9", password="password8", verified=True
        )
        pc = Player.objects.create(uid=carol, name="Carol")
        dave = User.objects.create_user(
            email="d9@x.com", nickname="d9", password="password8", verified=True
        )
        pd = Player.objects.create(uid=dave, name="Dave")
        Friendship.objects.create(requester=pa, addressee=pb)
        Friendship.objects.create(requester=pc, addressee=pa)
        Friendship.objects.create(requester=pa, addressee=pd)
        Friendship.objects.create(requester=pd, addressee=pa)

        data = api(client, "get", MS + "/friends", token=tokens["accessToken"]).json()
        assert [f["name"] for f in data["friends"]] == ["Dave"]
        assert [f["name"] for f in data["incomingRequests"]] == ["Carol"]
        assert [f["name"] for f in data["outgoingRequests"]] == ["Bob"]

    def test_etag_304(self, client, alice):
        tokens = auth(client)
        first = api(client, "get", MS + "/friends", token=tokens["accessToken"])
        etag = first["ETag"]
        second = client.get(
            MS + "/friends",
            headers={
                "Authorization": f"Bearer {tokens['accessToken']}",
                "If-None-Match": etag,
            },
        )
        assert second.status_code == 304
        assert second.content == b""


# ---- PUT /friends 状态机 ----


class TestFriendsUpdate:
    def _put(self, client, token, **payload):
        return api(client, "put", MS + "/friends", payload, token=token)

    def test_add_by_name_creates_outgoing(self, client, alice, bob):
        tokens = auth(client)
        resp = self._put(client, tokens["accessToken"], name="Bob", updateType="ADD")
        assert resp.status_code == 200
        assert [f["name"] for f in resp.json()["outgoingRequests"]] == ["Bob"]
        assert Friendship.objects.count() == 1

    def test_add_is_idempotent(self, client, alice, bob):
        tokens = auth(client)
        self._put(client, tokens["accessToken"], name="Bob", updateType="ADD")
        self._put(client, tokens["accessToken"], name="Bob", updateType="ADD")
        assert Friendship.objects.count() == 1

    def test_accept_incoming_via_add(self, client, alice, bob):
        ua, pa = alice
        ub, pb = bob
        Friendship.objects.create(requester=pb, addressee=pa)  # Bob 请求 Alice
        tokens = auth(client)
        resp = self._put(
            client, tokens["accessToken"],
            profileId=pb.uuid, updateType="ADD",
        )
        assert resp.status_code == 200
        assert [f["name"] for f in resp.json()["friends"]] == ["Bob"]
        # 双向两行
        assert Friendship.objects.count() == 2

    def test_add_self_rejected(self, client, alice):
        _u, pa = alice
        tokens = auth(client)
        resp = self._put(
            client, tokens["accessToken"], profileId=pa.uuid, updateType="ADD"
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "UNKNOWN_PROFILE"

    def test_unknown_name_400(self, client, alice):
        tokens = auth(client)
        resp = self._put(client, tokens["accessToken"], name="Ghost", updateType="ADD")
        assert resp.status_code == 400

    def test_case_insensitive_name_match(self, client, alice, bob):
        tokens = auth(client)
        resp = self._put(client, tokens["accessToken"], name="bob", updateType="ADD")
        assert resp.status_code == 200
        # 返回规范名（存储大小写）
        assert resp.json()["outgoingRequests"][0]["name"] == "Bob"

    def test_remove_friend_both_directions(self, client, alice, bob):
        _ua, pa = alice
        _ub, pb = bob
        Friendship.objects.create(requester=pa, addressee=pb)
        Friendship.objects.create(requester=pb, addressee=pa)
        tokens = auth(client)
        resp = self._put(
            client, tokens["accessToken"], profileId=pb.uuid, updateType="REMOVE"
        )
        assert resp.json()["friends"] == []
        assert Friendship.objects.count() == 0

    def test_remove_no_relation_idempotent(self, client, alice, bob):
        _ub, pb = bob
        tokens = auth(client)
        resp = self._put(
            client, tokens["accessToken"], profileId=pb.uuid, updateType="REMOVE"
        )
        assert resp.status_code == 200

    def test_invalid_update_type(self, client, alice):
        tokens = auth(client)
        resp = self._put(client, tokens["accessToken"], name="x", updateType="HACK")
        assert resp.status_code == 400


# ---- presence ----


class TestPresence:
    def test_self_report_and_friend_visibility(
        self, client, alice, bob
    ):
        ua, pa = alice
        ub, pb = bob
        Friendship.objects.create(requester=pa, addressee=pb)
        Friendship.objects.create(requester=pb, addressee=pa)

        ta = auth(client, "a@x.com")
        tb = auth(client, "b@x.com")

        # Bob 上报 ONLINE
        resp = api(
            client, "post", MS + "/presence",
            {"status": "ONLINE"}, token=tb["accessToken"],
        )
        assert resp.status_code == 200

        # Alice 上报 → 收到 Bob 状态
        resp = api(
            client, "post", MS + "/presence",
            {"status": "PLAYING_SERVER"}, token=ta["accessToken"],
        )
        data = resp.json()["presence"]
        assert len(data) == 1
        assert data[0]["profileId"] == pb.uuid
        assert data[0]["status"] == "ONLINE"
        assert data[0]["pmid"] == "0" * 32

    def test_stranger_not_visible(self, client, alice, bob):
        ub, _pb = bob
        ta = auth(client, "a@x.com")
        tb = auth(client, "b@x.com")
        api(client, "post", MS + "/presence", {"status": "ONLINE"},
            token=tb["accessToken"])
        # 非好友 → Alice 看不到
        resp = api(client, "post", MS + "/presence", {"status": "ONLINE"},
                   token=ta["accessToken"])
        assert resp.json()["presence"] == []

    def test_invalid_status(self, client, alice):
        tokens = auth(client)
        resp = api(client, "post", MS + "/presence", {"status": "HACKING"},
                   token=tokens["accessToken"])
        assert resp.status_code == 400


# ---- attributes 与开关 ----


class TestAttributes:
    def test_get_defaults(self, client, alice):
        tokens = auth(client)
        data = api(
            client, "get", MS + "/player/attributes", token=tokens["accessToken"]
        ).json()
        assert data["friendsPreferences"]["friends"] == "ENABLED"
        assert data["friendsPreferences"]["acceptInvites"] == "ENABLED"
        assert data["privileges"]["multiplayerServer"]["enabled"] is True

    def test_post_persists_settings(self, client, alice):
        ua, _ = alice
        tokens = auth(client)
        resp = api(
            client, "post", MS + "/player/attributes",
            {"friendsPreferences": {"friends": "ENABLED", "acceptInvites": "DISABLED"}},
            token=tokens["accessToken"],
        )
        assert resp.status_code == 200
        ua.refresh_from_db()
        assert ua.friends_enabled is True
        assert ua.accept_invites is False
        # 再 GET 回读
        data = api(
            client, "get", MS + "/player/attributes", token=tokens["accessToken"]
        ).json()
        assert data["friendsPreferences"]["acceptInvites"] == "DISABLED"

    def test_blocklist_empty(self, client, alice):
        tokens = auth(client)
        data = api(
            client, "get", MS + "/privacy/blocklist", token=tokens["accessToken"]
        ).json()
        assert data == {"blockedProfiles": []}

    def test_privileges_legacy(self, client, alice):
        tokens = auth(client)
        data = api(
            client, "get", MS + "/privileges", token=tokens["accessToken"]
        ).json()
        assert "privileges" in data

    def test_events_and_report_noop(self, client, db):
        assert client.post(MS + "/events", data="{}",
                           content_type="application/json").status_code == 204
        assert client.post(MS + "/player/report", data="{}",
                           content_type="application/json").status_code == 204

    def test_blocked_servers_404(self, client, db):
        assert client.get(BASE + "/sessionserver/blockedservers").status_code == 404


# ---- hello meta 开关 ----


class TestHelloFlag:
    def test_absent_when_disabled(self, client, db):
        data = client.get(BASE + "/").json()
        assert data["meta"].get("feature.enable_mojang_anti_features") is not True

    def test_present_when_enabled(self, client, db):
        Option.objects.set("friends_enabled", "1")
        data = client.get(BASE + "/").json()
        assert data["meta"]["feature.enable_mojang_anti_features"] is True


# ---- 限频 ----


class TestRateLimit:
    def test_429_with_retry_after(self, client, alice):
        Option.objects.set("friends_rate_limit", "2")
        tokens = auth(client)
        for _ in range(2):
            api(client, "get", MS + "/friends", token=tokens["accessToken"])
        resp = api(client, "get", MS + "/friends", token=tokens["accessToken"])
        assert resp.status_code == 429
        assert resp["Retry-After"] == "60"

    def test_zero_disables(self, client, alice):
        Option.objects.set("friends_rate_limit", "0")
        tokens = auth(client)
        for _ in range(5):
            resp = api(client, "get", MS + "/friends", token=tokens["accessToken"])
        assert resp.status_code == 200


# ---- CSRF（authlib-injector 代理不携带 CSRF token）----


class TestCsrfExempt:
    def test_put_friends_without_csrf(self, alice, bob):
        c = Client(enforce_csrf_checks=True)
        tokens = auth(c)
        resp = api(
            c, "put", MS + "/friends",
            {"name": "Bob", "updateType": "ADD"},
            token=tokens["accessToken"],
        )
        assert resp.status_code == 200, resp.content.decode()[:200]
        # ADD 在无反向申请时建立 outgoing 请求
        assert [f["name"] for f in resp.json()["outgoingRequests"]] == ["Bob"]

    def test_post_presence_without_csrf(self, alice):
        c = Client(enforce_csrf_checks=True)
        tokens = auth(c)
        resp = api(
            c, "post", MS + "/presence", {"status": "ONLINE"},
            token=tokens["accessToken"],
        )
        assert resp.status_code == 200, resp.content.decode()[:200]

    def test_post_attributes_without_csrf(self, alice):
        c = Client(enforce_csrf_checks=True)
        tokens = auth(c)
        resp = api(
            c, "post", MS + "/player/attributes",
            {"friendsPreferences": {"friends": "DISABLED", "acceptInvites": "ENABLED"}},
            token=tokens["accessToken"],
        )
        assert resp.status_code == 200, resp.content.decode()[:200]
