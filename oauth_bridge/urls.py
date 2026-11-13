"""oauth_bridge 路由：/oauth/microsoft/*（与 oauth2_provider 共用 /oauth/ 前缀，
子路径不冲突：本应用仅注册 microsoft/ 一节）。"""

from django.urls import path

from . import views

app_name = "oauth_bridge"

urlpatterns = [
    path("microsoft/login", views.msa_login, name="login"),
    path("microsoft/genuine", views.genuine_start, name="genuine"),
    path("microsoft/callback", views.msa_callback, name="callback"),
    path("microsoft/choose", views.msa_choose, name="choose"),
    path("microsoft/msa/unbind", views.msa_unbind, name="msa_unbind"),
    path(
        "microsoft/genuine/unbind",
        views.genuine_unbind,
        name="genuine_unbind",
    ),
]
