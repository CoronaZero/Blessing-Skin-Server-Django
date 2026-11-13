"""teximport 路由。"""

from django.urls import path

from . import views

app_name = "teximport"

urlpatterns = [
    path("skinlib/import", views.submit_import, name="submit"),
    path("admin/imports", views.imports_page, name="panel"),
    path("admin/imports/<int:item_id>/action", views.import_action, name="action"),
]
