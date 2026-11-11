"""options Admin：站点选项键值编辑。

对照原版 OptionForm 表单语义：值以文本编辑，类型由注册表
在读取端解析（文本编辑 + 搜索，分组展示由键名前缀约定承担）。
"""

from django.contrib import admin

from .models import Option


@admin.register(Option)
class OptionAdmin(admin.ModelAdmin):
    list_display = ("name", "value_preview")
    search_fields = ("name", "value")
    list_per_page = 50
    readonly_fields = ("name",)  # 键名不可改（引用完整性）

    @admin.display(description="值")
    def value_preview(self, obj):
        return obj.value[:60] + ("…" if len(obj.value) > 60 else "")
