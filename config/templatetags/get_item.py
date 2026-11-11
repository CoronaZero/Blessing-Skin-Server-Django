"""模板过滤器：{{ dict|get_item:key }}（面板选项渲染用）。"""

from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    return (dictionary or {}).get(key, "")
