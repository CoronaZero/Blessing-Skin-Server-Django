"""全站模板上下文处理器。

把 Option 站点名注入每个请求（消除各视图重复 get_value 与模板硬编码
"Blessing"），提供暗色模式所需的全局 JSON，并注入动态标签页图标：
登录用户 → 本人头像；未登录 → 站点默认皮肤头像；均不可用 → 静态占位。
"""

DEFAULT_SITE_NAME = "Blessing Skin"
FAVICON_FALLBACK = "/static/icons/user.svg"


def _favicon_url(request) -> str:
    """动态 favicon（64px PNG）：
    - 已登录：/avatar/me/64.png（自选头像优先，角色皮肤回落由端点处理），
      ?v=用户头像 tid 版本化（换头像后浏览器按新 URL 拉取）；
    - 未登录：站点默认皮肤头像 /avatar/texture/<tid>.png（端点仅渲染公开纹理），
      ?v=tid 版本化；
    - 兜底：静态占位图。
    """
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return f"/avatar/me/64.png?v={user.avatar or 0}"
    try:
        from options.models import Option

        raw = (Option.objects.get_value("default_skin_tid") or "0").strip()
        tid = int(raw)
    except Exception:  # noqa: BLE001 - options 表未建（安装向导前）或值非法
        return FAVICON_FALLBACK
    if tid > 0:
        return f"/avatar/texture/{tid}.png?v={tid}"
    return FAVICON_FALLBACK


def site(request):
    """全站上下文：站点名（未配置/表尚未迁移时回落默认值）+ 动态图标。"""
    site_name = DEFAULT_SITE_NAME
    try:
        from options.models import Option

        site_name = (Option.objects.get_value("site_name") or "").strip() or DEFAULT_SITE_NAME
    except Exception:  # noqa: BLE001 - 全新部署 options 表未建时（安装向导前）
        pass
    return {
        "site_name": site_name,
        "site_name_or_default": site_name,
        "favicon_url": _favicon_url(request),
    }
