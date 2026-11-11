"""accounts Admin：用户与密码重置令牌管理。"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import PasswordReset, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """用户管理：对照原版用户管理列表（搜索/过滤/封禁操作）。"""

    ordering = ("-uid",)
    list_display = ("uid", "email", "nickname", "permission", "score", "verified", "register_at")
    list_filter = ("permission", "verified")
    search_fields = ("email", "nickname")
    readonly_fields = ("uid", "register_at", "last_sign_at")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("资料", {"fields": ("nickname", "score", "avatar", "ip", "locale", "is_dark_mode")}),
        ("权限", {"fields": ("permission", "verified", "is_staff", "is_active", "groups")}),
        ("时间", {"fields": ("register_at", "last_sign_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "nickname", "password")}),
    )
    filter_horizontal = ("groups",)
    list_per_page = 30

    actions = ["ban_users", "unban_users"]

    @admin.action(description="封禁所选用户")
    def ban_users(self, request, queryset):
        queryset.update(permission=User.Permission.BANNED)
        self.message_user(request, f"已封禁 {queryset.count()} 个用户")

    @admin.action(description="解除封禁")
    def unban_users(self, request, queryset):
        queryset.update(permission=User.Permission.NORMAL)
        self.message_user(request, f"已解封 {queryset.count()} 个用户")


@admin.register(PasswordReset)
class PasswordResetAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "expires_at", "used_at", "is_expired")
    search_fields = ("user__email",)
    readonly_fields = ("token_hash",)
    list_filter = ("used_at",)

    @admin.display(boolean=True)
    def is_expired(self, obj):
        return obj.is_expired()
