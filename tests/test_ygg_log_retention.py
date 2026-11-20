"""Yggdrasil 日志保留天数（惰性清理）测试。"""

import pytest
from django.utils import timezone

from accounts.models import User
from yggdrasil.models import YggdrasilLog

pytestmark = pytest.mark.django_db


@pytest.fixture()
def super_client(client):
    User.objects.create_user(
        email="sp@x.com", nickname="sp", password="password8",
        permission=User.Permission.SUPER_ADMIN, verified=True,
    )
    client.login(username="sp@x.com", password="password8")
    return client


def _seed_log(days_ago: float) -> YggdrasilLog:
    from datetime import timedelta

    log = YggdrasilLog.objects.create(action="join")
    YggdrasilLog.objects.filter(id=log.id).update(
        time=timezone.now() - timedelta(days=days_ago)
    )
    return log


class TestRetentionCleanup:
    def test_cleanup_removes_expired_keeps_fresh(self, super_client):
        """保留 30 天：31 天前的被清、29 天前的保留（惰性：面板页访问触发）。"""
        from options.models import Option

        Option.objects.set("ygg_log_retention_days", "30")
        old = _seed_log(31)
        fresh = _seed_log(29)
        super_client.get("/admin/yggdrasil/logs")
        assert not YggdrasilLog.objects.filter(id=old.id).exists()
        assert YggdrasilLog.objects.filter(id=fresh.id).exists()

    def test_zero_or_blank_keeps_everything(self, super_client):
        """0 / 空 = 不清理（缺省行为不变，避免误删历史日志）。"""
        from options.models import Option

        Option.objects.set("ygg_log_retention_days", "0")
        old = _seed_log(365)
        super_client.get("/admin/yggdrasil/logs")
        assert YggdrasilLog.objects.filter(id=old.id).exists()

    def test_invalid_value_falls_back_no_cleanup(self, super_client):
        from options.models import Option

        Option.objects.set("ygg_log_retention_days", "abc")
        old = _seed_log(365)
        super_client.get("/admin/yggdrasil/logs")
        assert YggdrasilLog.objects.filter(id=old.id).exists()

    def test_log_write_path_throttled_cleanup(self, rf):
        """写入路径（_log）也带节流清理：有流量时即便管理员从不进面板，
        旧日志也最多每 6 小时被清一次。"""
        from django.test import override_settings

        from options.models import Option
        from yggdrasil.views import _log

        Option.objects.set("ygg_log_retention_days", "30")
        old = _seed_log(40)
        fresh = _seed_log(1)
        request = rf.post("/join")
        request.user = User.objects.create_user(
            email="u1@x.com", nickname="u1", password="password8"
        )
        with override_settings(YGG_VERBOSE_LOG=True):
            _log(request, "join", user=request.user)
        assert not YggdrasilLog.objects.filter(id=old.id).exists()
        assert YggdrasilLog.objects.filter(id=fresh.id).exists()

    def test_write_path_cleanup_throttled_by_cache(self, rf):
        """节流：cache 键存在时写入路径跳过清理（不重复 DELETE）。"""

        from django.core.cache import cache
        from django.test import override_settings

        from options.models import Option
        from yggdrasil.views import _log

        Option.objects.set("ygg_log_retention_days", "30")
        very_old = _seed_log(100)
        cache.set("ygg_log_cleanup_throttle", 1, 999)
        request = rf.post("/join")
        request.user = User.objects.create_user(
            email="u2@x.com", nickname="u2", password="password8"
        )
        with override_settings(YGG_VERBOSE_LOG=True):
            _log(request, "join", user=request.user)
        # 节流期内未清理
        assert YggdrasilLog.objects.filter(id=very_old.id).exists()
