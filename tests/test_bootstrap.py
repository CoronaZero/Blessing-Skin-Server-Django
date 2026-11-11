"""项目引导冒烟测试。

`check` 命令会校验 settings / urls / apps / middleware 的完整装配，
是检测骨架装配错误的最廉价手段（等价于 `python manage.py check`）。
"""

from django.core.management import call_command


def test_system_check_passes():
    """Django system check 全绿：settings/urlconf/apps 装配无误。"""
    call_command("check")


def test_prod_module_forces_debug_false():
    """prod settings 必须强制 DEBUG=False（生产入口防误配）。"""
    import config.settings.prod as prod

    assert prod.DEBUG is False


def test_wsgi_asgi_default_to_prod_settings():
    """wsgi/asgi 入口在生产容器中默认使用 prod 配置。"""
    import config.asgi as asgi_mod
    import config.wsgi as wsgi_mod

    assert wsgi_mod.default_settings == "config.settings.prod"
    assert asgi_mod.default_settings == "config.settings.prod"


def test_root_urlconf_contains_panel():
    from django.urls import reverse

    # /admin/ 已由自研面板接管（自带 admin 退役）
    assert reverse("panel:dashboard") == "/admin/"
