"""yggdrasil 路由（对照原版 routes.php 全端点）。"""

from django.urls import path

from . import minecraftservices as msvc
from . import views

app_name = "yggdrasil"

urlpatterns = [
    path("", views.hello, name="hello"),
    path("minecraftservices/publickeys", views.get_public_keys, name="publickeys"),
    # ---- 好友系统（规范：{API_ROOT}minecraftservices/*）----
    path("minecraftservices/friends", msvc.friends_endpoint, name="msvc_friends"),
    path("minecraftservices/presence", msvc.presence_update, name="msvc_presence"),
    path(
        "minecraftservices/player/attributes",
        msvc.player_attributes, name="msvc_attributes",
    ),
    path("minecraftservices/privileges", msvc.privileges_legacy, name="msvc_privileges"),
    path("minecraftservices/privacy/blocklist", msvc.blocklist, name="msvc_blocklist"),
    path("minecraftservices/events", msvc.events_noop, name="msvc_events"),
    path("minecraftservices/player/report", msvc.report_noop, name="msvc_report"),
    path("sessionserver/blockedservers", msvc.blocked_servers, name="blocked_servers"),
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
