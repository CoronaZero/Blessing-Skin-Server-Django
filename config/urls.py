"""根 URL 配置。Phase 0 仅保留 Django Admin 占位，后续 Phase 逐个挂载路由。"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from . import views


def healthz(request):
    """容器存活探针。"""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("", views.home, name="home"),
    path("setup/", include("setup.urls")),
    path("", include("accounts.urls")),
    path("", include("textures.urls")),
    path("", include("players.urls")),
    # OAuth2（对照原版 Passport /oauth/token 等端点）
    path("oauth/", include("oauth2_provider.urls", namespace="oauth2_provider")),
    path("api/yggdrasil/", include("yggdrasil.urls")),
    path("admin/", admin.site.urls),
]
