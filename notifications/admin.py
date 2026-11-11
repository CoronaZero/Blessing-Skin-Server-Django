"""notifications Admin：站内通知管理。"""

from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "recipient", "title", "read_at", "created_at")
    search_fields = ("title", "content", "recipient__email")
    list_filter = ("created_at",)
    list_select_related = ("recipient",)
    list_per_page = 30
