"""textures 路由（对照原版静态纹理路由组）。"""

from django.urls import path, re_path

from . import views

app_name = "textures"


def _page_or_submit(page, submit):
    def handler(request):
        if request.method == "GET":
            return page(request)
        return submit(request)

    return handler


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
    # 同 URL：GET 渲染上传页 / POST 走上传 API
    path(
        "skinlib/upload",
        _page_or_submit(views.upload_page, views.upload),
        name="upload",
    ),
    path("textures/<str:hash_value>", views.serve_texture, name="texture"),
    # 皮肤立绘预览（对照原版 /preview/{tid}；另支持 hash 定位）
    path("preview/<int:tid>", views.preview_texture, name="preview"),
    path("avatar/<int:size>/<str:player_name>", views.serve_avatar, name="avatar"),
    # CustomSkinAPI R1 根路径兜底：/{player}.json（注册在根 urls最后）
    re_path(r"^(?P<player_name>[\w\-.]+)\.json$", views.player_profile_json, name="player_json"),
    # ---- 用户中心：衣柜 / 举报 ----
    # 同 URL 三协商：浏览器直达=衣柜页 / AJAX=JSON / POST=添加
    path("user/closet", views.closet_collection, name="closet"),
    path("user/closet/<int:item_id>/remove", views.closet_remove, name="closet_remove"),
    path("user/closet/<int:item_id>/name", views.closet_rename, name="closet_rename"),
    path("user/report", views.report_submit, name="report"),
]
