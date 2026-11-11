"""setup 路由（挂载于 /setup 前缀）。"""

from django.urls import path

from . import views

app_name = "setup"


def _page_or_submit(page, submit):
    def handler(request):
        if request.method == "GET":
            return page(request)
        return submit(request)

    return handler


urlpatterns = [
    path("", _page_or_submit(views.wizard, views.submit), name="wizard"),
]
