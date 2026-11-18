"""panel 权限装饰器（独立模块，避免循环导入）。"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotFound

from accounts.models import User


def _is_admin(user):
    return user.is_authenticated and user.permission >= User.Permission.ADMIN


def _is_super(user):
    return user.is_authenticated and user.permission >= User.Permission.SUPER_ADMIN


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not _is_admin(request.user):
            return HttpResponseNotFound()
        return view(request, *args, **kwargs)

    return wrapper


def super_admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not _is_super(request.user):
            return HttpResponseNotFound()
        return view(request, *args, **kwargs)

    return wrapper


def _panel_ctx(request, active):
    """面板页面公共上下文（放本模块避免 views <-> 独立视图文件循环导入）。

    is_super 必须按真实权限计算——曾因硬编码 True 导致普通管理员
    在邀请码页看到「站点选项 / Yggdrasil 日志」的超管专属侧边栏入口。
    """
    from options.models import Option

    return {
        "active": active,
        "is_super": _is_super(request.user),
        "site_name": Option.objects.get_value("site_name") or "Blessing Skin",
    }
