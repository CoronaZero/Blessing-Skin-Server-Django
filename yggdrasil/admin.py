"""yggdrasil Admin：令牌与访问日志管理。"""

from django.contrib import admin

from .models import YggdrasilLog, YggdrasilToken


@admin.register(YggdrasilToken)
class YggdrasilTokenAdmin(admin.ModelAdmin):
    list_display = (
        "owner", "client_token", "is_valid",
        "is_refreshable", "created_at", "profile_id",
    )
    search_fields = ("owner__email", "access_token", "client_token")
    list_select_related = ("owner",)
    readonly_fields = ("access_token", "created_at")
    list_per_page = 30

    actions = ["revoke_tokens"]

    @admin.display(boolean=True)
    def is_valid(self, obj):
        return obj.is_valid()

    @admin.display(boolean=True)
    def is_refreshable(self, obj):
        return obj.is_refreshable()

    @admin.action(description="吊销所选令牌")
    def revoke_tokens(self, request, queryset):
        count, _ = queryset.delete()
        self.message_user(request, f"已吊销 {count} 个令牌")


@admin.register(YggdrasilLog)
class YggdrasilLogAdmin(admin.ModelAdmin):
    list_display = ("action", "user", "player", "ip", "time")
    search_fields = ("action", "user__email", "player__name", "ip")
    list_filter = ("action", "time")
    list_select_related = ("user", "player")
    readonly_fields = ("action", "user", "player", "parameters", "ip", "time")
    list_per_page = 50

    def has_add_permission(self, request):
        return False  # 审计日志只读
