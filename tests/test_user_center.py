"""用户中心 API 测试（TDD 先行）。

对照原版 UserController / PlayerController / ClosetController / ReportController：
- /user/profile         GET 页 / POST 修改资料（nickname/password/email/delete）
- /user/players         角色列表/添加/删除/改名/设置皮肤
- /user/closet          衣柜列表/添加/重命名/删除
- /user/notification    通知列表/已读
- /user/report          提交举报
- /user/sign            每日签到
"""

import pytest
from django.utils import timezone

from accounts.models import User
from moderation.models import Report
from notifications.models import Notification
from players.models import Player
from textures.models import Texture

pytestmark = pytest.mark.django_db


@pytest.fixture()
def user():
    return User.objects.create_user(
        email="u@x.com", nickname="OriginalNick", password="password8", score=50
    )


@pytest.fixture()
def client_logged(client, user):
    client.force_login(user)
    return client


# ---- 资料修改 ----


class TestProfile:
    def test_change_nickname(self, client_logged, user):
        resp = client_logged.post(
            "/user/profile", {"action": "nickname", "new_nickname": "NewNick"}
        )
        assert resp.json()["code"] == 0
        user.refresh_from_db()
        assert user.nickname == "NewNick"

    def test_change_nickname_length_limit(self, client_logged):
        """昵称长度上限 32（原版 nickname varchar 语义 + 安全审查修复）。"""
        resp = client_logged.post(
            "/user/profile", {"action": "nickname", "new_nickname": "长" * 50}
        )
        assert resp.json()["code"] == 1

    def test_change_password_requires_current(self, client_logged, user):
        resp = client_logged.post("/user/profile", {
            "action": "password",
            "current_password": "wrong-pass",
            "new_password": "newpassword9",
        })
        assert resp.json()["code"] == 1

    def test_change_password_success(self, client_logged, user):
        resp = client_logged.post("/user/profile", {
            "action": "password",
            "current_password": "password8",
            "new_password": "newpassword9",
        })
        assert resp.json()["code"] == 0
        user.refresh_from_db()
        assert user.verify_password("newpassword9")

    def test_change_email_conflict(self, client_logged, user):
        User.objects.create_user(email="taken@x.com", nickname="t", password="x")
        resp = client_logged.post("/user/profile", {
            "action": "email", "email": "taken@x.com", "password": "password8",
        })
        assert resp.json()["code"] == 1

    def test_delete_account_requires_password(self, client_logged, user):
        resp = client_logged.post("/user/profile", {
            "action": "delete", "password": "wrong-pass",
        })
        assert resp.json()["code"] == 1
        assert User.objects.filter(uid=user.uid).exists()

    def test_delete_account_cascades(self, client_logged, user):
        Player.objects.create(uid=user, name="DelMe")
        resp = client_logged.post("/user/profile", {
            "action": "delete", "password": "password8",
        })
        assert resp.json()["code"] == 0
        assert not User.objects.filter(uid=user.uid).exists()
        assert not Player.objects.filter(name="DelMe").exists()

    def test_admin_cannot_delete_self(self, client_logged, user):
        user.permission = User.Permission.ADMIN
        user.save()
        resp = client_logged.post("/user/profile", {
            "action": "delete", "password": "password8",
        })
        assert resp.json()["code"] == 1


# ---- 角色管理 ----


class TestPlayers:
    def test_list_players(self, client_logged, user):
        Player.objects.create(uid=user, name="Steve")
        resp = client_logged.get("/user/players", headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.json()["code"] == 0
        names = [p["name"] for p in resp.json()["data"]]
        assert "Steve" in names

    def test_add_player(self, client_logged, user):
        resp = client_logged.post("/user/players", {"name": "NewPlayer"})
        assert resp.json()["code"] == 0
        assert Player.objects.filter(name="NewPlayer", uid=user).exists()

    def test_add_duplicate_name_rejected(self, client_logged, user):
        Player.objects.create(uid=user, name="Taken")
        resp = client_logged.post("/user/players", {"name": "Taken"})
        assert resp.json()["code"] == 1

    def test_player_limit_per_user(self, client_logged, user):
        """single-player-limit 内嵌能力：option 限制每人角色数。"""
        from options.models import Option

        Option.objects.set("player_limit_per_user", "1")
        Player.objects.create(uid=user, name="First")
        resp = client_logged.post("/user/players", {"name": "Second"})
        assert resp.json()["code"] == 1

    def test_delete_player(self, client_logged, user):
        player = Player.objects.create(uid=user, name="ByeBye")
        resp = client_logged.post(f"/user/players/{player.pid}/delete")
        assert resp.json()["code"] == 0
        assert not Player.objects.filter(pid=player.pid).exists()

    def test_delete_other_users_player_forbidden(self, client_logged, user):
        other = User.objects.create_user(
            email="v@x.com", nickname="v", password="password8"
        )
        player = Player.objects.create(uid=other, name="NotYours")
        resp = client_logged.post(f"/user/players/{player.pid}/delete")
        assert resp.json()["code"] == 1
        assert Player.objects.filter(pid=player.pid).exists()

    def test_rename_player(self, client_logged, user):
        player = Player.objects.create(uid=user, name="OldName")
        resp = client_logged.post(f"/user/players/{player.pid}/name", {"name": "NewName"})
        assert resp.json()["code"] == 0
        player.refresh_from_db()
        assert player.name == "NewName"

    def test_set_skin(self, client_logged, user):
        from textures.storage import texture_storage

        player = Player.objects.create(uid=user, name="SkinTest")
        texture = Texture.objects.create(
            name="t", type="steve", hash="a" * 64, size=1,
            uploader=user, public=True,
        )
        texture_storage.save_bytes(texture.hash, b"png" * 10)
        resp = client_logged.post(f"/user/players/{player.pid}/skin", {
            "tid": texture.tid,
        })
        assert resp.json()["code"] == 0
        player.refresh_from_db()
        assert player.tid_skin == texture


# ---- 衣柜 ----


class TestCloset:
    @pytest.fixture()
    def texture(self, user):
        return Texture.objects.create(
            name="公共皮肤", type="steve", hash="b" * 64, size=1,
            uploader=user, public=True,
        )

    def test_list_empty(self, client_logged):
        resp = client_logged.get("/user/closet", headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.json()["code"] == 0
        assert resp.json()["data"] == []

    def test_add_to_closet(self, client_logged, user, texture):
        resp = client_logged.post("/user/closet", {
            "tid": texture.tid, "name": "备注名",
        })
        assert resp.json()["code"] == 0
        item = user.closet.first()
        assert item.texture == texture
        assert item.item_name == "备注名"

    def test_add_to_closet_score_cost(self, client_logged, user, texture):
        from options.models import Option

        Option.objects.set("score_per_closet_item", "5")
        before = user.score
        resp = client_logged.post("/user/closet", {"tid": texture.tid})
        assert resp.json()["code"] == 0
        user.refresh_from_db()
        assert user.score == before - 5

    def test_duplicate_closet_rejected(self, client_logged, user, texture):
        client_logged.post("/user/closet", {"tid": texture.tid})
        resp = client_logged.post("/user/closet", {"tid": texture.tid})
        assert resp.json()["code"] == 1

    def test_rename_closet_item(self, client_logged, user, texture):
        from textures.models import ClosetItem

        ClosetItem.objects.create(owner=user, texture=texture, item_name="旧名")
        item = user.closet.first()
        resp = client_logged.post(f"/user/closet/{item.id}/name", {"name": "新名"})
        assert resp.json()["code"] == 0
        item.refresh_from_db()
        assert item.item_name == "新名"

    def test_remove_from_closet(self, client_logged, user, texture):
        from textures.models import ClosetItem

        item = ClosetItem.objects.create(owner=user, texture=texture)
        resp = client_logged.post(f"/user/closet/{item.id}/remove")
        assert resp.json()["code"] == 0
        assert not ClosetItem.objects.filter(id=item.id).exists()


# ---- 通知 ----


class TestNotifications:
    def test_list(self, client_logged, user):
        Notification.objects.create(recipient=user, title="欢迎", content="哈哈")
        resp = client_logged.get(
            "/user/notifications",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.json()["code"] == 0
        assert len(resp.json()["data"]) == 1
        assert resp.json()["data"][0]["title"] == "欢迎"

    def test_list_time_localized_and_formatted(self, client_logged, user):
        """时间按 settings.TIME_ZONE 本地化为 Y-m-d H:i（不再 ISO+偏移）。"""
        import datetime as _dt

        from django.test import override_settings

        n = Notification.objects.create(recipient=user, title="t", content="c")
        # 固定 UTC 时刻：2026-11-13 06:30 UTC → 北京 14:30
        n.created_at = _dt.datetime(2026, 11, 13, 6, 30, tzinfo=_dt.UTC)
        n.save(update_fields=["created_at"])
        with override_settings(TIME_ZONE="Asia/Shanghai"):
            resp = client_logged.get(
                "/user/notifications",
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        assert resp.json()["data"][0]["created_at"] == "2026-11-13 14:30"

    def test_mark_read(self, client_logged, user):
        n = Notification.objects.create(recipient=user, title="t", content="c")
        resp = client_logged.post(f"/user/notifications/{n.id}/read")
        assert resp.json()["code"] == 0
        n.refresh_from_db()
        assert n.read_at is not None

    def test_read_all_only_own_notifications(self, client_logged, user):
        """一键全部已读只作用于本人通知（他人通知不受影响）。"""
        mine_unread = Notification.objects.create(recipient=user, title="m1", content="c")
        mine_read = Notification.objects.create(recipient=user, title="m2", content="c")
        mine_read.read_at = timezone.now()
        mine_read.save()
        other = User.objects.create_user(
            email="other9@x.com", nickname="other9", password="password8"
        )
        other_unread = Notification.objects.create(recipient=other, title="o1", content="c")

        resp = client_logged.post("/user/notifications/read-all")
        assert resp.json()["code"] == 0

        mine_unread.refresh_from_db()
        other_unread.refresh_from_db()
        assert mine_unread.read_at is not None  # 我的未读 → 已读
        assert other_unread.read_at is None  # 他人的绝不动

    def test_read_all_idempotent(self, client_logged, user):
        """重复调用幂等，返回 0 条时也成功。"""
        resp = client_logged.post("/user/notifications/read-all")
        assert resp.json()["code"] == 0
        assert "0 条" in resp.json()["message"]

    def test_read_all_requires_login(self, client):
        assert client.post("/user/notifications/read-all").status_code == 302

    def test_other_users_notification_forbidden(self, client_logged, user):
        other = User.objects.create_user(
            email="n2@x.com", nickname="n2", password="password8"
        )
        n = Notification.objects.create(recipient=other, title="x", content="y")
        resp = client_logged.post(f"/user/notifications/{n.id}/read")
        assert resp.json()["code"] == 1

    def test_page_container_matches_js_selector(self, client_logged, user):
        """UI 契约：通知页容器须与 user_notifications.js 的选择器一致。

        真实事故：BS5 迁移把模板 .bs-card-body 改成 .card-body，
        JS 仍查旧类 → box=null → TypeError 静默失败，页面空白
        （API 层测试全绿拦不住，此测试锁前端两侧一致性）。
        """
        html = client_logged.get("/user/notifications").content.decode()
        assert 'id="notifications-list"' in html
        # 模板容器类
        assert '<div class="card-body"></div>' in html
        # JS 选择器同侧（静态文件级锁定）
        from pathlib import Path as _P

        js = _P("static/js/user_notifications.js").read_text(encoding="utf-8")
        assert '#notifications-list .card-body' in js
        assert ".bs-card-body" not in js  # 旧类选择器不得回流

    def test_notifications_page_sets_csrf_cookie(self, client_logged, user):
        """页面必须种下 csrftoken cookie（页面内 POST 的前置条件）。

        真实事故：通知页无表单，浏览器若无 csrftoken cookie 则
        全部已读/单条已读 POST 一律 403（返回 HTML，前端 JSON 解析
        失败）→ 点击"无反应"。ensure_csrf_cookie 保证 cookie 存在。
        """
        resp = client_logged.get("/user/notifications")
        assert resp.status_code == 200
        assert "csrftoken" in resp.cookies


# ---- 举报 ----


class TestReports:
    def test_submit_report(self, client_logged, user):
        texture = Texture.objects.create(
            name="t", type="steve", hash="c" * 64, size=1,
            uploader=user, public=True,
        )
        resp = client_logged.post("/user/report", {
            "tid": texture.tid, "reason": "含违规内容",
        })
        assert resp.json()["code"] == 0
        report = Report.objects.first()
        assert report.reason == "含违规内容"
        assert report.reporter == user

    def test_report_requires_reason(self, client_logged, user):
        texture = Texture.objects.create(
            name="t", type="steve", hash="d" * 64, size=1,
            uploader=user, public=True,
        )
        resp = client_logged.post("/user/report", {"tid": texture.tid, "reason": ""})
        assert resp.json()["code"] == 1


# ---- 签到 ----


class TestSign:
    def test_daily_sign_awards_score(self, client_logged, user):
        from options.models import Option

        Option.objects.set("score_per_sign", "10")
        before = user.score
        from datetime import timedelta

        from django.utils import timezone

        user.last_sign_at = timezone.now() - timedelta(days=2)
        user.save()
        resp = client_logged.post("/user/sign")
        assert resp.json()["code"] == 0
        user.refresh_from_db()
        assert user.score == before + 10

    def test_double_sign_rejected(self, client_logged, user):
        client_logged.post("/user/sign")
        resp = client_logged.post("/user/sign")
        assert resp.json()["code"] == 1
