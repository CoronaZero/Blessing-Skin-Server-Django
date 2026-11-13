"""应用配置。"""

from django.apps import AppConfig


class TeximportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "teximport"
    verbose_name = "纹理导入"
