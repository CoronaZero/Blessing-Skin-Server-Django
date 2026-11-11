"""notifications 应用：站内通知。

对照原版 Laravel notifications：morph 结构重设计为直接关联收件人
（站点内自用通知，无跨模型多态需求）；前端消费字段
（title/content/read_at/created_at）保持兼容。
"""

from django.conf import settings
from django.db import models


class NotificationQuerySet(models.QuerySet):
    def unread(self):
        return self.filter(read_at__isnull=True)

    def read(self):
        return self.filter(read_at__isnull=False)


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField("标题", max_length=255)
    content = models.TextField("内容", default="", blank=True)
    read_at = models.DateTimeField("已读时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        verbose_name = "通知"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def mark_as_read(self):
        from django.utils import timezone

        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at"])
