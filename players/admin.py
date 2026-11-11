"""players Admin：角色管理。"""

from django.contrib import admin

from .models import Player


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ("pid", "name", "uid", "uuid", "skin", "cape", "last_modified")
    search_fields = ("name", "uuid", "uid__email")
    list_filter = ("last_modified",)
    list_select_related = ("uid", "tid_skin", "tid_cape")
    readonly_fields = ("uuid", "last_modified")
    list_per_page = 30

    @admin.display(description="皮肤")
    def skin(self, obj):
        return obj.tid_skin.hash[:8] + "…" if obj.tid_skin else "—"

    @admin.display(description="披风")
    def cape(self, obj):
        return obj.tid_cape.hash[:8] + "…" if obj.tid_cape else "—"
