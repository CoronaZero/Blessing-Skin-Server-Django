"""invitation 应用：邀请码注册（内嵌原 invitation-codes 插件能力）。

管理后台开关控制（Phase 6），模型仅承载码语料与占用状态。
"""

import hashlib
import secrets

from django.conf import settings
from django.db import models


def generate_invitation_code() -> str:
    """对照原版 md5(Str::random())：CSPRNG 种子的 md5 十六进制。"""
    return hashlib.md5(secrets.token_bytes(32)).hexdigest()


class InvitationCode(models.Model):
    code = models.CharField(
        "邀请码", max_length=32, unique=True, default=generate_invitation_code
    )
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="used_invitation_codes",
    )
    used_at = models.DateTimeField("使用时间", null=True, blank=True)
    generated_at = models.DateTimeField("生成时间", auto_now_add=True)

    class Meta:
        verbose_name = "邀请码"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.code[:8] + "…"

    def save(self, *args, **kwargs):
        # 占用即落时间戳（业务层只需设置 used_by）
        if self.used_by_id and self.used_at is None:
            from django.utils import timezone

            self.used_at = timezone.now()
        super().save(*args, **kwargs)

    @property
    def is_used(self) -> bool:
        return self.used_by is not None
