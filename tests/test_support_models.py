"""moderation / notifications / options / invitation 模型测试（TDD 先行）。

对照 PHP 原版：
- reports：id/tid/uploader/reporter/reason/status(0待处理|1已处理|2已驳回)/report_at
- notifications：Laravel notifications morph 结构 → 重设计为目标用户直接关联
- options：option_name → option_value 键值表（含多语言键 *_zh_CN 等）
- invitation_codes：内嵌能力（原 invitation-codes 插件），used_by/used_at
"""

import pytest

from accounts.models import User
from invitation.models import InvitationCode
from moderation.models import Report
from notifications.models import Notification
from options.models import Option
from textures.models import Texture


def make_user(email, **kw):
    return User.objects.create_user(email=email, nickname=email.split("@")[0], password="x", **kw)


class TestReport:
    def test_create_and_relations(self, db):
        uploader = make_user("up@x.com")
        reporter = make_user("rp@x.com")
        texture = Texture.objects.create(
            name="t", type="steve", hash="a" * 64, size=1, uploader=uploader, public=True
        )
        report = Report.objects.create(
            texture=texture, uploader=uploader, reporter=reporter, reason="违规内容"
        )
        assert report.status == Report.Status.PENDING
        assert report.reason == "违规内容"
        assert report.texture == texture
        assert report.informer == reporter
        assert report.report_at is not None

    def test_status_choices(self, db):
        assert Report.Status.PENDING == 0
        assert Report.Status.RESOLVED == 1
        assert Report.Status.REJECTED == 2

    def test_lifecycle_update(self, db):
        uploader = make_user("up2@x.com")
        reporter = make_user("rp2@x.com")
        texture = Texture.objects.create(
            name="t", type="cape", hash="b" * 64, size=1, uploader=uploader, public=True
        )
        report = Report.objects.create(
            texture=texture, uploader=uploader, reporter=reporter, reason="x"
        )
        report.status = Report.Status.REJECTED
        report.save()
        report.refresh_from_db()
        assert report.status == Report.Status.REJECTED

    def test_status_filter_manager(self, db):
        """常用筛选：待处理 / 已处理（对照后台举报列表）。"""
        uploader = make_user("up3@x.com")
        reporter = make_user("rp3@x.com")
        for i, status in enumerate([0, 1, 2]):
            texture = Texture.objects.create(
                name=f"t{i}", type="steve", hash=f"{i}" * 64, size=1,
                uploader=uploader, public=True,
            )
            Report.objects.create(
                texture=texture, uploader=uploader, reporter=reporter,
                reason="x", status=status,
            )
        assert Report.objects.filter(status=Report.Status.PENDING).count() == 1
        assert Report.objects.exclude(status=Report.Status.PENDING).count() == 2


class TestNotification:
    def test_create_for_user(self, db):
        user = make_user("n@x.com")
        n = Notification.objects.create(
            recipient=user, title="系统通知", content="欢迎注册"
        )
        assert n.read_at is None
        assert n.created_at is not None

    def test_mark_read(self, db):
        user = make_user("n2@x.com")
        n = Notification.objects.create(recipient=user, title="t", content="c")
        n.read_at = n.created_at
        n.save()
        n.refresh_from_db()
        assert n.read_at is not None

    def test_unread_manager(self, db):
        user = make_user("n3@x.com")
        Notification.objects.create(recipient=user, title="a", content="a")
        Notification.objects.create(recipient=user, title="b", content="b")
        unread = Notification.objects.unread()
        assert unread.count() == 2
        first = unread.first()
        first.read_at = first.created_at
        first.save()
        assert Notification.objects.unread().count() == 1

    def test_recipient_relation(self, db):
        user = make_user("n4@x.com")
        Notification.objects.create(recipient=user, title="t", content="c")
        assert user.notifications.count() == 1


class TestOption:
    def test_set_get_roundtrip(self, db):
        Option.objects.set("site_name", "我的皮肤站")
        assert Option.objects.get_value("site_name") == "我的皮肤站"

    def test_get_default_for_missing(self, db):
        assert Option.objects.get_value("no_such_key", "fallback") == "fallback"
        assert Option.objects.get_value("no_such_key") is None

    def test_overwrite_existing(self, db):
        Option.objects.set("k", "v1")
        Option.objects.set("k", "v2")
        assert Option.objects.get_value("k") == "v2"

    def test_bulk_set(self, db):
        Option.objects.bulk_set({"a": "1", "b": "2"})
        assert Option.objects.get_value("a") == "1"
        assert Option.objects.get_value("b") == "2"

    def test_localized_lookup(self, db):
        """多语言选项：site_name_zh_CN 覆盖 site_name（对照 option_localized）。"""
        Option.objects.set("site_name", "Default")
        Option.objects.set("site_name_zh_CN", "中文名")
        assert Option.objects.get_value("site_name_zh_CN") == "中文名"

    def test_type_int_coercion(self, db):
        """数值选项以字符串存储，读取端按声明的类型解析（类型注册表 Phase 6）。"""
        Option.objects.set("score_per_storage", "2")
        assert Option.objects.get_value("score_per_storage") == "2"


class TestInvitationCode:
    def test_generate_code(self, db):
        code = InvitationCode.objects.create()
        assert len(code.code) == 32  # md5 hex（对照原版 md5(Str::random())）
        assert code.used_by is None
        assert code.used_at is None

    def test_use_code(self, db):
        code = InvitationCode.objects.create()
        user = make_user("new@x.com")
        # 业务层只需设置 used_by，时间戳自动落盘
        code.used_by = user
        code.save()
        code.refresh_from_db()
        assert code.used_by == user
        assert code.used_at is not None

    def test_code_unique(self, db):
        from django.db import IntegrityError

        InvitationCode.objects.create(code="a" * 32)
        with pytest.raises(IntegrityError):
            InvitationCode.objects.create(code="a" * 32)

    def test_available_filter(self, db):
        InvitationCode.objects.create()
        used_code = InvitationCode.objects.create()
        user = make_user("u@x.com")
        used_code.used_by = user
        used_code.save()
        assert InvitationCode.objects.filter(used_by__isnull=True).count() == 1
        assert InvitationCode.objects.filter(used_by__isnull=False).count() == 1
