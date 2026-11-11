"""players 路由：用户角色管理端点。"""

from django.urls import path

from . import views

app_name = "players"

urlpatterns = [
    # 同 URL GET 列表 / POST 新增
    path("user/players", views.players_collection, name="collection"),
    path("user/players/<int:pid>/delete", views.delete_player, name="delete"),
    path("user/players/<int:pid>/name", views.rename_player, name="rename"),
    path("user/players/<int:pid>/skin", views.set_texture_skin, name="set_skin"),
    path("user/players/<int:pid>/cape", views.set_texture_cape, name="set_cape"),
]
