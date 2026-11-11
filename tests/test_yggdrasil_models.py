"""yggdrasil 模型测试（TDD 先行）。

对照 yggdrasil-api 插件原设计 + v2 重设计：
- Token 原版存 Cache（驱逐即全员掉线、多 worker 不一致）→ 改 DB 持久化
- 结构：owner clientToken/accessToken(JWT)/profileId/created_at/expire 轨迹
- ygg_log：action/user_id/player_id/parameters/ip/time
"""


from accounts.models import User
from players.models import Player
from yggdrasil.models import YggdrasilLog, YggdrasilToken


def make_user(email="y@x.com"):
    return User.objects.create_user(email=email, nickname=email.split("@")[0], password="x")


class TestTokenModel:
    def test_create_token(self, db):
        user = make_user()
        token = YggdrasilToken.objects.create(
            owner=user,
            client_token="client-hex",
            access_token="jwt-value",
        )
        assert token.owner == user
        assert token.profile_id == ""
        assert token.created_at is not None

    def test_token_validity_window(self, db):
        """有效期属性：created_at + expire_1 秒内有效（选项值 Phase 6 注入，模型按字段算）。"""
        from datetime import timedelta

        from django.utils import timezone

        user = make_user()
        token = YggdrasilToken.objects.create(
            owner=user, client_token="c", access_token="a", expires_in=259200
        )
        assert token.is_valid() is True
        token.created_at = timezone.now() - timedelta(seconds=259201)
        token.save()
        assert token.is_valid() is False

    def test_refreshable_window(self, db):
        """刷新窗口长于有效期（原版 ygg_token_expire_2 > expire_1）。"""
        from datetime import timedelta

        from django.utils import timezone

        user = make_user()
        token = YggdrasilToken.objects.create(
            owner=user, client_token="c", access_token="a",
            expires_in=60, refresh_expires_in=3600,
        )
        token.created_at = timezone.now() - timedelta(seconds=120)
        token.save()
        assert token.is_valid() is False
        assert token.is_refreshable() is True
        token.created_at = timezone.now() - timedelta(seconds=3700)
        token.save()
        assert token.is_refreshable() is False

    def test_access_token_indexed(self, db):
        user = make_user()
        YggdrasilToken.objects.create(owner=user, client_token="c", access_token="tok-1")
        assert YggdrasilToken.objects.filter(access_token="tok-1").exists()

    def test_profile_id_optional(self, db):
        user = make_user()
        token = YggdrasilToken.objects.create(
            owner=user, client_token="c", access_token="t", profile_id="abcd" * 8
        )
        assert token.profile_id == "abcd" * 8

    def test_revoke(self, db):
        user = make_user()
        YggdrasilToken.objects.create(owner=user, client_token="c", access_token="t")
        revoked, _ = YggdrasilToken.objects.filter(owner=user).delete()
        assert revoked == 1


class TestYggdrasilLog:
    def test_create_log_entry(self, db):
        user = make_user()
        log = YggdrasilLog.objects.create(
            action="authenticate",
            user=user,
            parameters='{"agent":"minecraft"}',
            ip="203.0.113.10",
        )
        assert log.action == "authenticate"
        assert log.time is not None

    def test_player_id_optional(self, db):
        user = make_user()
        player = Player.objects.create(uid=user, name="LogTest")
        log = YggdrasilLog.objects.create(
            action="join", user=user, player=player, parameters="{}", ip="127.0.0.1"
        )
        assert log.player == player

    def test_recent_logs_ordering(self, db):
        """默认排序（Meta：-time, -id），同微秒内创建也稳定。"""
        user = make_user()
        for action in ("authenticate", "refresh", "validate"):
            YggdrasilLog.objects.create(
                action=action, user=user, parameters="{}", ip="127.0.0.1"
            )
        actions = list(YggdrasilLog.objects.values_list("action", flat=True))
        assert actions == ["validate", "refresh", "authenticate"]
