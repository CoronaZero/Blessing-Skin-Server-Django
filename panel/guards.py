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
