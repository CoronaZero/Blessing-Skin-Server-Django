"""模板源码契约扫描：Django 模板层低级语法事故防复发。

已发生的事故：{# #} 注释写成跨行——Django 该语法不支持换行（官方文档
"A comment cannot span multiple lines"），未闭合的注释按纯文本原样
渲染进页面（站点选项页用户可见）。扫描所有模板：出现 {# 的行必须在
本行内闭合 #}；跨行注释须改用 {% comment %} 块。
"""

from pathlib import Path

TEMPLATE_DIR = Path("templates")


def _templates() -> list[Path]:
    return sorted(TEMPLATE_DIR.rglob("*.html"))


class TestSingleLineComments:
    def test_all_templates_have_no_multiline_comment_opens(self):
        offenders = []
        for tpl in _templates():
            for i, line in enumerate(tpl.read_text(encoding="utf-8").splitlines(), 1):
                # 本行出现 {# 但未闭合 #}（跨行注释是非法语法，会被原样渲染）
                if "{#" in line and "#}" not in line:
                    offenders.append(f"{tpl}:{i}")
        assert not offenders, (
            f"跨行 {{# #}} 注释会被原样渲染进页面，改单行或 {{% comment %}}：{offenders}"
        )

    def test_templates_dir_exists(self):
        assert list(_templates()), "模板目录为空——扫描本身失效"
