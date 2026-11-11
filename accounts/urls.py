"""accounts 路由：/auth/* 全部认证端点（GET 页面 / POST 提交单视图分发）。"""

from django.urls import path

from . import views

app_name = "accounts"


def _page_or_submit(page, submit):
    def handler(request):
        if request.method == "GET":
            return page(request)
        return submit(request)

    return handler


urlpatterns = [
    path(
        "auth/login",
        _page_or_submit(views.login_page, views.login_submit),
        name="login",
    ),
    path(
        "auth/register",
        _page_or_submit(views.register_page, views.register_submit),
        name="register",
    ),
    path("auth/logout", views.logout_submit, name="logout"),
    path(
        "auth/forgot",
        _page_or_submit(views.forgot_page, views.forgot_submit),
        name="forgot",
    ),
    path(
        "auth/reset",
        _page_or_submit(views.reset_page, views.reset_submit),
        name="reset_page",
    ),
    path("auth/verify/<str:token>", views.verify_email, name="verify"),
    # ---- 用户中心（分页：仪表盘/资料/角色列表/衣柜/通知）----
    path("user", views.user_dashboard, name="user_center"),
    path("user/profile", views.profile_view, name="profile"),
    path("user/notifications", views.notifications_list, name="notifications"),
    path("user/notifications/page", views.notifications_page, name="notifications_page"),
    path(
        "user/notifications/<int:notification_id>/read",
        views.notification_read,
        name="notification_read",
    ),
    path("user/sign", views.daily_sign, name="sign"),
]
