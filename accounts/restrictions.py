"""未验证用户访问限制（管理面板开关控制）。

开关（选项组「未验证用户限制」，默认全关）：
- unverified_block_skinlib：禁止浏览皮肤库（列表 + 搜索 + 详情）
- unverified_block_upload：禁止上传皮肤（页面 + API）
- unverified_block_ygg：禁止使用 Yggdrasil API（authenticate 403）
- unverified_block_import：禁止站外导入皮肤

语义：仅对「已登录但 verified=False」的用户生效；管理员与已验证用户不受影响。
"""

from django.http import HttpResponse
from django.shortcuts import render

from options.models import Option

_BOOL_TRUE = ("1", "true", "on")


def option_enabled(name: str) -> bool:
    return (Option.objects.get_value(name) or "0").strip().lower() in _BOOL_TRUE


def blocked(request, option_key: str) -> bool:
    """当前请求是否命中指定的未验证限制。"""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    if user.verified or user.is_admin():
        return False
    return option_enabled(option_key)


def restricted_page(request, reason: str) -> HttpResponse:
    """受限提示页（403，页面与 API 共用友好提示）。"""
    return render(
        request,
        "restricted.html",
        {
            "site_name": Option.objects.get_value("site_name"),
            "reason": reason,
        },
        status=403,
    )
