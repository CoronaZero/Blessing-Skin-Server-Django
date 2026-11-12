"""公告 Markdown 测试（定向）。"""

import pytest

from accounts.models import User
from options.models import Option

pytestmark = pytest.mark.django_db


@pytest.fixture()
def logged(client):
    User.objects.create_user(email="u@x.com", nickname="u", password="password8")
    client.login(username="u@x.com", password="password8")
    return client


class TestAnnouncement:
    def test_markdown_rendered_on_dashboard(self, logged):
        Option.objects.set(
            "site_announcement", "# 标题\n\n**加粗** 与 [链接](https://example.com)"
        )
        html = logged.get("/user").content.decode()
        assert "<h1>标题</h1>" in html
        assert "<strong>加粗</strong>" in html
        assert "查看全部" in html

    def test_announcement_page_full_view(self, logged):
        Option.objects.set("site_announcement", "- 列表项一\n- 列表项二")
        html = logged.get("/user/announcement").content.decode()
        assert "<li>列表项一</li>" in html

    def test_empty_announcement_placeholder(self, logged):
        html = logged.get("/user/announcement").content.decode()
        assert "暂未发布公告" in html

    def test_option_editable_in_panel(self, client, db):
        User.objects.create_superuser(
            email="op@x.com", nickname="OP", password="password8"
        )
        client.login(username="op@x.com", password="password8")
        html = client.get("/admin/options/general").content.decode()
        assert "site_announcement" in html
