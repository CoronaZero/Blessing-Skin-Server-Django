"""全站模板上下文处理器。

把 Option 站点名注入每个请求（消除各视图重复 get_value 与模板硬编码
"Blessing"），并提供暗色模式所需的全局 JSON。
"""

DEFAULT_SITE_NAME = "Blessing Skin"


def site(request):
    """全站上下文：站点名（未配置/表尚未迁移时回落默认值）。"""
    site_name = DEFAULT_SITE_NAME
    try:
        from options.models import Option

        site_name = (Option.objects.get_value("site_name") or "").strip() or DEFAULT_SITE_NAME
    except Exception:  # noqa: BLE001 - 全新部署 options 表未建时（安装向导前）
        pass
    return {
        "site_name": site_name,
        "site_name_or_default": site_name,
    }
