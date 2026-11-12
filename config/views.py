"""项目首页与通用页面（对照原版 HomeController）。"""

import re

from django.shortcuts import render
from django.utils.html import escape

from options.models import Option


def _sanitize_bg_url(raw: str) -> str:
    """背景图 URL 白名单校验：仅 http(s) 绝对地址，防 javascript: 注入。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if re.match(r"^https?://[^\s'\"]+$", raw, re.IGNORECASE):
        return escape(raw)
    return ""


def home(request):
    """首页：站点介绍 + 常规登录入口（fast-login 内嵌能力）。"""
    return render(
        request,
        "home.html",
        {
            "site_name": Option.objects.get_value("site_name"),
            "site_description": Option.objects.get_value("site_description"),
            "home_bg_url": _sanitize_bg_url(
                Option.objects.get_value("home_bg_url") or ""
            ),
        },
    )
