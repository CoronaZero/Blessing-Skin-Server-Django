"""签到状态化测试（定向）。"""

import pytest

from accounts.models import User

pytestmark = pytest.mark.django_db


class TestSignButtonState:
    def test_signed_today_shows_disabled_with_time(self, client, db):
        from django.utils import timezone

        user = User.objects.create_user(
            email="s@x.com", nickname="s", password="password8"
        )
        user.last_sign_at = timezone.now()  # 20h 内
        user.save()
        client.force_login(user)
        html = client.get("/user").content.decode()
        assert "disabled" in html
        assert "后可再签" in html

    def test_not_signed_shows_active_button(self, client, db):
        from datetime import timedelta

        from django.utils import timezone

        user = User.objects.create_user(
            email="s2@x.com", nickname="s2", password="password8"
        )
        user.last_sign_at = timezone.now() - timedelta(days=2)
        user.save()
        client.force_login(user)
        html = client.get("/user").content.decode()
        assert "每日签到" in html
        assert "后可再签" not in html
