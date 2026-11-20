"""模板源码与渲染契约扫描：Django 模板注释低级语法事故防复发。

已发生的事故（两次，同一行注释）：
① {# #} 写成跨行——该语法不支持换行，未闭合注释按纯文本渲染进页面；
② "修复①"时把注释定界符字面量写进了注释内容——lexer 正则
  ({#.*?#}) 非贪婪匹配到注释内嵌套的 #} 提前闭合，其后半段
  又按纯文本渲染（且注释在 for 循环内，每张卡片漏一次）。
"""

from pathlib import Path

import pytest
from django.core.cache import cache

TEMPLATE_DIR = Path("templates")


def _templates() -> list[Path]:
    return sorted(TEMPLATE_DIR.rglob("*.html"))


class TestTemplateComments:
    def test_no_multiline_comment_opens(self):
        """事故 ① 防复发：出现 {# 的行必须在本行闭合 #}。"""
        offenders = []
        for tpl in _templates():
            for i, line in enumerate(tpl.read_text(encoding="utf-8").splitlines(), 1):
                if "{#" in line and "#}" not in line:
                    offenders.append(f"{tpl}:{i}")
        assert not offenders, (
            f"跨行 {{# #}} 注释会被原样渲染进页面，改单行或 {{% comment %}}：{offenders}"
        )

    def test_no_nested_comment_delimiters(self):
        """事故 ② 防复发：一行内注释定界符各至多一次。

        lexer 非贪婪匹配——注释内容里再出现 {# 或 #} 会提前截断，
        残留半段按纯文本渲染。嵌套计数必然 >1，直接禁止。
        """
        offenders = []
        for tpl in _templates():
            for i, line in enumerate(tpl.read_text(encoding="utf-8").splitlines(), 1):
                if line.count("{#") > 1 or line.count("#}") > 1:
                    offenders.append(f"{tpl}:{i} {line.strip()[:60]}")
        assert not offenders, (
            f"注释嵌套定界符会被 lexer 提前截断渲染：{offenders}"
        )

    def test_templates_dir_exists(self):
        assert list(_templates()), "模板目录为空——扫描本身失效"


class TestOptionsPageRendersNoCommentLeak:
    """渲染级行为锁：站点选项页 HTML 不得含注释实现细节文本。"""

    @pytest.fixture(autouse=True)
    def _super(self, client):
        from accounts.models import User

        User.objects.create_user(
            email="sp@x.com", nickname="sp", password="password8",
            permission=User.Permission.SUPER_ADMIN, verified=True,
        )
        client.login(username="sp@x.com", password="password8")
        cache.clear()
        yield client

    @pytest.mark.django_db
    @pytest.mark.parametrize("section", ["general", "users", "services"])
    def test_comment_text_never_reaches_html(self, _super, section):
        """循环内注释泄漏会逐卡片重复出现——任何分区都不该见到注释字样。"""
        html = _super.get(f"/admin/options/{section}").content.decode()
        assert "紧凑多列" not in html, "模板注释文本泄漏进页面"
        assert "勿嵌套" not in html, "模板注释文本泄漏进页面"
        assert " #}" not in html, "注释定界符残留泄漏进页面"
