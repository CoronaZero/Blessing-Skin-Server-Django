"""根级辅助视图。"""


def admin_login_redirect(request):
    """旧 /admin/login/ 跳转面板（自带 admin 已退役）。"""
    from django.shortcuts import redirect

    return redirect("/auth/login?next=/admin/")
