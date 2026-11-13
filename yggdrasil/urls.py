"""yggdrasil 路由（对照原版 routes.php 全端点）。"""

from django.urls import path

from . import views

app_name = "yggdrasil"

urlpatterns = [
    path("", views.hello, name="hello"),
    path("minecraftservices/publickeys", views.get_public_keys, name="publickeys"),
    path("authserver/authenticate", views.authenticate, name="authenticate"),
    path("authserver/refresh", views.refresh, name="refresh"),
    path("authserver/validate", views.validate, name="validate"),
    path("authserver/invalidate", views.invalidate, name="invalidate"),
    path("authserver/signout", views.signout, name="signout"),
    path(
        "sessionserver/session/minecraft/join",
        views.join,
        name="join",
    ),
    path(
        "sessionserver/session/minecraft/hasJoined",
        views.has_joined,
        name="has_joined",
    ),
    path(
        "sessionserver/session/minecraft/profile/<str:uuid>",
        views.get_profile,
        name="profile",
    ),
    path("api/profiles/minecraft", views.search_profiles, name="search_profiles"),
    path(
        "api/users/profiles/minecraft/<str:username>",
        views.search_single_profile,
        name="search_single",
    ),
    path(
        "api/user/profile/<str:uuid>/<str:type_>",
        views.upload_texture,
        name="upload_texture",
    ),
]
