"""快速配置启动器（拖拽按钮）测试。

对照原版 yggdrasil-api 插件 dnd 板块：ygg_show_config_section 开关、
标题/提示语/按钮文案面板可配（原版硬编码 lang 文件）、拖拽协议
"authlib-injector:yggdrasil-server:<url>"（启动器识别关键）。
"""

import pytest

from accounts.models import User
from options.models import Option

pytestmark = pytest.mark.django_db


@pytest.fixture()
def logged(client):
    User.objects.create_user(
        email="u@x.com", nickname="u", password="password8", verified=True
    )
    client.force_login(User.objects.get(email="u@x.com"))
    return client


class TestLauncherDndCard:
    def test_hidden_when_disabled(self, logged):
        resp = logged.get("/user")
        assert b"ygg-dnd-button" not in resp.content

    def test_shown_with_defaults_when_enabled(self, logged):
        Option.objects.set("ygg_show_config_section", "1")
        html = logged.get("/user").content.decode()
        assert 'id="ygg-dnd-button"' in html
        assert "快速配置启动器" in html  # 默认标题
        assert "将此按钮拖动至启动器" in html  # 默认按钮文字
        # 默认提示语 + API 地址展示
        assert "认证服务器地址" in html
        assert "/api/yggdrasil/" in html
        # 拖拽按钮属性
        assert 'draggable="true"' in html
        assert 'data-api-url=' in html

    def test_all_texts_admin_configurable(self, logged):
        """四项文案全部可后台修改（原版不可改，本版核心需求）。"""
        Option.objects.set("ygg_show_config_section", "1")
        Option.objects.set("ygg_dnd_title", "一键添加服务器")
        Option.objects.set("ygg_dnd_tip", "拖走这个按钮，马上起飞！")
        Option.objects.set("ygg_dnd_button", "拖我！")
        html = logged.get("/user").content.decode()
        assert "一键添加服务器" in html
        assert "拖走这个按钮，马上起飞！" in html
        assert "拖我！" in html
        # 默认文案不再出现
        assert "快速配置启动器" not in html
        assert "将此按钮拖动至启动器" not in html

    def test_blank_texts_fall_back_to_defaults(self, logged):
        Option.objects.set("ygg_show_config_section", "1")
        Option.objects.set("ygg_dnd_title", "  ")
        Option.objects.set("ygg_dnd_tip", "")
        Option.objects.set("ygg_dnd_button", "")
        html = logged.get("/user").content.decode()
        assert "快速配置启动器" in html
        assert "将此按钮拖动至启动器" in html

    def test_api_url_uses_site_url_option(self, logged):
        Option.objects.set("ygg_show_config_section", "1")
        Option.objects.set("site_url", "https://skin.example.com")
        html = logged.get("/user").content.decode()
        assert "https://skin.example.com/api/yggdrasil/" in html


class TestDndProtocolContract:
    """拖拽协议契约（启动器识别关键，源码级锁定）。"""

    def test_dragstart_uses_authlib_protocol(self):
        from pathlib import Path

        js = Path("static/js/user_dashboard.js").read_text(encoding="utf-8")
        assert "authlib-injector:yggdrasil-server:" in js
        assert "encodeURIComponent" in js
        assert 'setData("text/plain"' in js or "setData('text/plain'" in js

    def test_click_falls_back_to_exec_command(self):
        """非 HTTPS 环境剪贴板 API 不可用，须有 execCommand 回落。"""
        from pathlib import Path

        js = Path("static/js/user_dashboard.js").read_text(encoding="utf-8")
        assert "execCommand" in js
