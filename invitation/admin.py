"""invitation Admin：邀请码管理（内嵌能力开关配套）。"""

from django.contrib import admin

from .models import InvitationCode


@admin.register(InvitationCode)
class InvitationCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "is_used", "used_by", "used_at", "generated_at")
    search_fields = ("code",)
    list_filter = ("used_at",)
    list_select_related = ("used_by",)
    readonly_fields = ("code", "generated_at")
    list_per_page = 50

    actions = ["generate_batch"]

    @admin.action(description="生成 10 个新邀请码")
    def generate_batch(self, request, queryset):
        for _ in range(10):
            InvitationCode.objects.create()
        self.message_user(request, "已生成 10 个邀请码")
