"""oauth_bridge 模型层测试：绑定关系约束与解绑冷却计算。"""

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import User
from oauth_bridge.models import MojangBinding, MsaIdentity, UnbindCooldown

pytestmark = pytest.mark.django_db


def make_user(email="u@x.com"):
    return User.objects.create_user(email=email, nickname="u", password="password8")


class TestMsaIdentity:
    def test_user_one_to_one(self):
        user = make_user()
        MsaIdentity.objects.create(user=user, sub="sub-a", email="a@live.com")
        with pytest.raises(IntegrityError), transaction.atomic():
            MsaIdentity.objects.create(user=user, sub="sub-b")

    def test_sub_unique_across_users(self):
        MsaIdentity.objects.create(user=make_user("a@x.com"), sub="sub-a")
        with pytest.raises(IntegrityError), transaction.atomic():
            MsaIdentity.objects.create(user=make_user("b@x.com"), sub="sub-a")

    def test_related_name(self):
        user = make_user()
        identity = MsaIdentity.objects.create(
            user=user, sub="sub-a", email="a@live.com", display_name="A"
        )
        assert user.msa_identity is identity


class TestMojangBinding:
    def test_user_one_to_one(self):
        user = make_user()
        MojangBinding.objects.create(user=user, mc_uuid="a" * 32, mc_name="Notch")
        with pytest.raises(IntegrityError), transaction.atomic():
            MojangBinding.objects.create(user=user, mc_uuid="b" * 32, mc_name="Jeb")

    def test_mc_uuid_unique_across_users(self):
        MojangBinding.objects.create(
            user=make_user("a@x.com"), mc_uuid="a" * 32, mc_name="Notch"
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            MojangBinding.objects.create(
                user=make_user("b@x.com"), mc_uuid="a" * 32, mc_name="Notch"
            )

    def test_cascade_on_user_delete(self):
        user = make_user()
        MsaIdentity.objects.create(user=user, sub="sub-a")
        MojangBinding.objects.create(user=user, mc_uuid="a" * 32, mc_name="Notch")
        UnbindCooldown.record(user, "msa")
        uid = user.uid
        user.delete()
        assert not MsaIdentity.objects.filter(user_id=uid).exists()
        assert not MojangBinding.objects.filter(user_id=uid).exists()
        assert not UnbindCooldown.objects.filter(user_id=uid).exists()


class TestUnbindCooldown:
    def test_no_records_means_no_cooldown(self):
        user = make_user()
        assert UnbindCooldown.remaining_seconds(user, "msa", days=7) == 0

    def test_cooldown_active(self):
        user = make_user()
        UnbindCooldown.record(user, "genuine")
        assert UnbindCooldown.remaining_seconds(user, "genuine", days=7) > 0

    def test_cooldown_expired(self):
        user = make_user()
        record = UnbindCooldown.record(user, "genuine")
        # 把解绑时间拨回 3 天前，冷却 2 天应已过期
        UnbindCooldown.objects.filter(pk=record.pk).update(
            unbound_at=timezone.now() - timezone.timedelta(days=3)
        )
        assert UnbindCooldown.remaining_seconds(user, "genuine", days=2) == 0

    def test_zero_days_disables_cooldown(self):
        user = make_user()
        UnbindCooldown.record(user, "msa")
        assert UnbindCooldown.remaining_seconds(user, "msa", days=0) == 0

    def test_kind_isolated(self):
        user = make_user()
        UnbindCooldown.record(user, "msa")
        assert UnbindCooldown.remaining_seconds(user, "genuine", days=7) == 0

    def test_latest_record_wins(self):
        user = make_user()
        first = UnbindCooldown.record(user, "msa")
        UnbindCooldown.objects.filter(pk=first.pk).update(
            unbound_at=timezone.now() - timezone.timedelta(days=10)
        )
        UnbindCooldown.record(user, "msa")
        # 最新记录在冷却期内
        assert UnbindCooldown.remaining_seconds(user, "msa", days=7) > 0
