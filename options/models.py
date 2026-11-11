"""options 应用：站点选项键值存储。

对照 PHP 原版 options 表（option_name/option_value 键值对）与
option()/option_localized() 助手语义。值一律以字符串存储，
类型解析由消费端声明的注册表负责（Phase 6 Admin 渲染）。
"""

from django.db import models


class OptionManager(models.Manager):
    def set(self, name: str, value) -> None:
        """设置（存在则覆盖）。对照原版 option(['k' => 'v'])。"""
        text_value = "" if value is None else str(value)
        self.update_or_create(name=name, defaults={"value": text_value})

    def bulk_set(self, mapping: dict) -> None:
        for name, value in mapping.items():
            self.set(name, value)

    def get_value(self, name: str, default=None):
        """读取；不存在返回 default。对照原版 option('k', default)。"""
        try:
            return self.get(name=name).value
        except self.model.DoesNotExist:
            return default


class Option(models.Model):
    name = models.CharField("选项名", max_length=100, unique=True)
    value = models.TextField("选项值", default="")

    objects = OptionManager()

    class Meta:
        verbose_name = "站点选项"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.name} = {self.value[:50]}"
