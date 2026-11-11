"""textures Admin：纹理与衣柜管理。"""

from django.contrib import admin

from .models import ClosetItem, Texture


@admin.register(Texture)
class TextureAdmin(admin.ModelAdmin):
    list_display = ("tid", "name", "type", "hash_short", "size", "uploader", "public", "likes", "upload_at")
    search_fields = ("name", "hash", "uploader__email")
    list_filter = ("type", "public", "upload_at")
    list_select_related = ("uploader",)
    readonly_fields = ("tid", "hash", "upload_at")
    list_per_page = 30

    actions = ["make_public", "make_private"]

    @admin.display(description="SHA-256")
    def hash_short(self, obj):
        return obj.hash[:12] + "…"

    @admin.action(description="设为公开")
    def make_public(self, request, queryset):
        queryset.update(public=True)
        self.message_user(request, f"已公开 {queryset.count()} 个纹理")

    @admin.action(description="设为私密")
    def make_private(self, request, queryset):
        queryset.update(public=False)
        self.message_user(request, f"已转私密 {queryset.count()} 个纹理")


@admin.register(ClosetItem)
class ClosetItemAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "texture", "item_name", "added_at")
    search_fields = ("owner__email", "item_name", "texture__name")
    list_select_related = ("owner", "texture")
    list_per_page = 30
