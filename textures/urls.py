"""textures 路由（对照原版静态纹理路由组）。"""

from django.urls import path, re_path

from . import views

app_name = "textures"

urlpatterns = [
    path(
        "skinlib",
        views.skinlib_page,
        name="skinlib_page",
    ),
    path(
        "skinlib/show",
        views.texture_show_page,
        name="texture_show",
    ),
    path("skinlib/search", views.search, name="search"),
    path("skinlib/upload", views.upload, name="upload"),
    path("textures/<str:hash_value>", views.serve_texture, name="texture"),
    path("avatar/<int:size>/<str:player_name>", views.serve_avatar, name="avatar"),
    # CustomSkinAPI R1 根路径兜底：/{player}.json（注册在根 urls最后）
    re_path(r"^(?P<player_name>[\w\-.]+)\.json$", views.player_profile_json, name="player_json"),
    # ---- 用户中心：衣柜 / 举报 ----
    path("user/closet", views.closet_collection, name="closet"),
    path("user/closet/<int:item_id>/name", views.closet_rename, name="closet_rename"),
    path("user/closet/<int:item_id>/remove", views.closet_remove, name="closet_remove"),
    path("user/report", views.report_submit, name="report"),
]
