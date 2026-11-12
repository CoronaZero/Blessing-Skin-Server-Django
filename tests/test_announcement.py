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

    def test_more_tag_truncation(self, logged):
        Option.objects.set(
            "site_announcement",
            "# 上半部分\n\n摘要内容\n\n<!--more-->\n\n# 下半部分\n\n展开后内容",
        )
        html = logged.get("/user").content.decode()
        assert "摘要内容" in html
        assert "展开后内容" not in html
        assert "阅读全文" in html
        # 独立页显示全文
        full = logged.get("/user/announcement").content.decode()
        assert "展开后内容" in full

    def test_no_more_tag_shows_all(self, logged):
        Option.objects.set("site_announcement", "无截断标记的公告")
        html = logged.get("/user").content.decode()
        assert "无截断标记的公告" in html
        assert "阅读全文" not in html

    def test_option_editable_in_panel(self, client, db):
        User.objects.create_superuser(
            email="op@x.com", nickname="OP", password="password8"
        )
        client.login(username="op@x.com", password="password8")
        html = client.get("/admin/options/general").content.decode()
        assert "site_announcement" in html
