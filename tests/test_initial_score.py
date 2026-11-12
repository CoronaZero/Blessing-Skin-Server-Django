"""新用户初始积分测试（定向）。"""

import pytest

from accounts.models import User
from options.models import Option

pytestmark = pytest.mark.django_db


class TestInitialScore:
    def test_custom_initial_score_awarded(self, client):
        Option.objects.set("user_initial_score", "100")
        resp = client.post("/auth/register", {
            "email": "new@x.com", "password": "password8", "nickname": "N",
        })
        assert resp.json()["code"] == 0
        user = User.objects.get(email="new@x.com")
        assert user.score == 100

    def test_zero_initial_score_default(self, client):
        Option.objects.set("user_initial_score", "0")
        resp = client.post("/auth/register", {
            "email": "z@x.com", "password": "password8", "nickname": "Z",
        })
        assert resp.json()["code"] == 0
        user = User.objects.get(email="z@x.com")
        assert user.score == 0
