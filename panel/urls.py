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
    path("options/ygg-key/generate", views.ygg_key_generate, name="ygg_key_generate"),
    path("options/<str:section>", views.options_page, name="options_section"),
    path("broadcast", views.broadcast, name="broadcast"),
    path("yggdrasil/logs", views.yggdrasil_logs, name="yggdrasil_logs"),
    path("invitations", views.invitations_page, name="invitations"),
    path("invitations/generate", views.invitations_generate, name="invitations_generate"),
    path("players", views.players_page, name="players"),
    path("players/<int:pid>/delete", views.player_delete, name="player_delete"),
    path("players/<int:pid>/<str:kind>", views.player_set_texture, name="player_set_texture"),
]
