"""panel 路由（/admin/ 前缀，替代自带 admin）。"""

from django.urls import path

from . import views

app_name = "panel"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("users", views.users, name="users"),
    path("users/<int:uid>/action", views.user_action, name="user_action"),
    path("textures", views.textures, name="textures"),
    path("textures/<int:tid>/action", views.texture_action, name="texture_action"),
    path("reports", views.reports, name="reports"),
    path("reports/<int:rid>/action", views.report_action, name="report_action"),
    path("options", views.options_page, name="options"),
    path("options/save", views.options_save, name="options_save"),
    path("options/captcha-test", views.captcha_test, name="captcha_test"),
    path("options/mail-test", views.mail_test, name="mail_test"),
    path("options/<str:section>", views.options_page, name="options_section"),
    path("broadcast", views.broadcast, name="broadcast"),
]
