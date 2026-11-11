"""项目首页与通用页面（对照原版 HomeController）。"""

from django.shortcuts import render

from options.models import Option


def home(request):
    """首页：站点介绍 + 快捷登录（fast-login 内嵌能力）。"""
    return render(
        request,
        "home.html",
        {
            "site_name": Option.objects.get_value("site_name"),
            "site_description": Option.objects.get_value("site_description"),
        },
    )
