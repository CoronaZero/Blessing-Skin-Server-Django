"""导入请求数据模型与跨进程 worker 锁。"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class ImportRequest(models.Model):
    """单个站外纹理的拉取请求。

    生命周期：
      pending（待审核，仅 import_require_review 开启时）→ queued →
      processing → done / failed / dedup（站内已存在）
      pending → rejected（管理员拒绝）
    source_url 在入队前完成追踪参数剥离，详情页直接展示该链接。
    """

    class Status(models.TextChoices):
        PENDING = "pending", "待审核"
        QUEUED = "queued", "待拉取"
        PROCESSING = "processing", "拉取中"
        DONE = "done", "已完成"
        DEDUP = "dedup", "站内已存在"
        FAILED = "failed", "失败"
        REJECTED = "rejected", "已拒绝"

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="import_requests",
    )
    source_url = models.CharField("原始链接（净化后）", max_length=500)
    site = models.CharField("来源站点", max_length=255, default="")
    tid = models.IntegerField("来源 TID", default=0)
    status = models.CharField(
        "状态", max_length=12, choices=Status.choices, default=Status.QUEUED
    )
    error_text = models.CharField("失败原因", max_length=255, default="", blank=True)
    texture = models.ForeignKey(
        "textures.Texture",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_requests",
    )
    score_cost = models.IntegerField("消耗积分", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    processed_at = models.DateTimeField("处理时间", null=True, blank=True)

    class Meta:
        verbose_name = "导入请求"
        verbose_name_plural = verbose_name
        ordering = ["-id"]

    def __str__(self):
        return f"#{self.id} {self.status} {self.source_url[:60]}"

    @property
    def terminal(self) -> bool:
        return self.status in (
            self.Status.DONE, self.Status.DEDUP, self.Status.FAILED,
            self.Status.REJECTED,
        )


class WorkerLock(models.Model):
    """跨进程互斥锁（单行表，pk=1）。

    全站同时仅允许一个线程拉取：多 gunicorn worker 部署下，
    threading.Lock 无法跨进程，改用 DB 条件 UPDATE 原子抢占；
    locked_until 过期后自动可被抢占（worker 崩溃自愈）。
    """

    id = models.IntegerField(primary_key=True, default=1)
    holder = models.CharField("持有者标识", max_length=64, default="")
    locked_until = models.DateTimeField("锁到期时间", null=True, blank=True)

    class Meta:
        verbose_name = "导入 worker 锁"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"worker-lock({self.holder or 'free'})"

    @classmethod
    def acquire(cls, holder: str, ttl_seconds: int) -> bool:
        """尝试抢占；未过期且非空则失败（原子 UPDATE，跨进程安全）。"""
        expired = timezone.now()
        # 未初始化行：先插（并发下重复插入靠主键冲突免疫）
        cls.objects.get_or_create(id=1)
        rows = cls.objects.filter(id=1).filter(
            # 可抢占：无持有者 或 已过期
            models.Q(locked_until__isnull=True)
            | models.Q(locked_until__lt=expired)
        ).update(
            holder=holder,
            locked_until=expired + timezone.timedelta(seconds=ttl_seconds),
        )
        return rows > 0

    @classmethod
    def release(cls, holder: str) -> None:
        cls.objects.filter(id=1, holder=holder).update(
            holder="", locked_until=None
        )
