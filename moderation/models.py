"""moderation 应用：纹理举报。"""

from django.conf import settings
from django.db import models


class Report(models.Model):
    """对照 PHP 原版 reports 表。"""

    class Status(models.IntegerChoices):
        PENDING = 0, "待处理"
        RESOLVED = 1, "已处理"
        REJECTED = 2, "已驳回"

    id = models.AutoField(primary_key=True)
    texture = models.ForeignKey(
        "textures.Texture", on_delete=models.CASCADE, related_name="reports"
    )
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reports_about_my_textures",
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reports_filed",
    )
    reason = models.TextField("举报理由")
    status = models.IntegerField(
        "状态", choices=Status.choices, default=Status.PENDING
    )
    report_at = models.DateTimeField("举报时间", auto_now_add=True)

    class Meta:
        verbose_name = "举报"
        verbose_name_plural = verbose_name
        ordering = ["-report_at"]

    def __str__(self):
        return f"Report #{self.id} ({self.get_status_display()})"

    @property
    def informer(self):
        """对照原版 informer 关系（举报人）。"""
        return self.reporter
