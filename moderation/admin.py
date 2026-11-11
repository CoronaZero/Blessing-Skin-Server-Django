"""moderation Admin：举报处理。"""

from django.contrib import admin

from .models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("id", "texture", "uploader", "reporter", "reason", "status", "report_at")
    search_fields = ("reason", "texture__name", "reporter__email")
    list_filter = ("status", "report_at")
    list_select_related = ("texture", "uploader", "reporter")
    readonly_fields = ("report_at",)
    list_per_page = 30

    actions = ["resolve", "reject"]

    @admin.action(description="标记为已处理")
    def resolve(self, request, queryset):
        queryset.update(status=Report.Status.RESOLVED)
        self.message_user(request, f"已处理 {queryset.count()} 条举报")

    @admin.action(description="驳回举报")
    def reject(self, request, queryset):
        queryset.update(status=Report.Status.REJECTED)
        self.message_user(request, f"已驳回 {queryset.count()} 条举报")
