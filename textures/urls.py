"""textures 路由（对照原版静态纹理路由组）。"""

from django.urls import path, re_path

from . import views

app_name = "textures"

urlpatterns = [
    path("skinlib/search", views.search, name="search"),
    path("skinlib/upload", views.upload, name="upload"),
    path("textures/<str:hash_value>", views.serve_texture, name="texture"),
    path("avatar/<int:size>/<str:player_name>", views.serve_avatar, name="avatar"),
    # CustomSkinAPI R1 根路径兜底：/{player}.json（注册在根 urls最后）
    re_path(r"^(?P<player_name>[\w\-.]+)\.json$", views.player_profile_json, name="player_json"),
]
