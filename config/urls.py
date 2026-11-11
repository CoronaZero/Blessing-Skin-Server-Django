"""根 URL 配置。Phase 0 仅保留 Django Admin 占位，后续 Phase 逐个挂载路由。"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def healthz(request):
    """容器存活探针。"""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("", include("accounts.urls")),
    path("", include("textures.urls")),
    path("api/yggdrasil/", include("yggdrasil.urls")),
    path("admin/", admin.site.urls),
]
