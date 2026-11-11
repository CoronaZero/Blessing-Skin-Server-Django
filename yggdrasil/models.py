"""yggdrasil 应用：authlib-injector 认证服务的令牌与日志。

对照 yggdrasil-api 插件（原版 Token 存 Cache）重设计为 DB 持久化：
- 避免缓存驱逐导致全员掉线
- 多 worker 部署一致
- 便于 Admin 管理（Phase 6）
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class YggdrasilToken(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="yggdrasil_tokens",
    )
    client_token = models.CharField("客户端令牌", max_length=64)
    access_token = models.CharField(
        "访问令牌(JWT)", max_length=512, db_index=True
    )
    profile_id = models.CharField("当前角色 UUID", max_length=32, default="", blank=True)
    # 有效期与刷新窗口（create/refresh 时由选项注入；默认对照原版 3 天 / 7 天）
    expires_in = models.PositiveIntegerField("有效期(秒)", default=259200)
    refresh_expires_in = models.PositiveIntegerField("刷新窗口(秒)", default=604800)
    created_at = models.DateTimeField("签发时间", default=timezone.now)

    class Meta:
        verbose_name = "Yggdrasil 令牌"
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["owner", "access_token"])]

    def __str__(self):
        return f"ygg-token({self.owner_id})"

    def is_valid(self) -> bool:
        """对照原版 Token::isValid。"""
        deadline = self.created_at + timezone.timedelta(seconds=self.expires_in)
        return timezone.now() <= deadline

    def is_refreshable(self) -> bool:
        """对照原版 Token::isRefreshable。"""
        deadline = self.created_at + timezone.timedelta(seconds=self.refresh_expires_in)
        return timezone.now() <= deadline


class YggdrasilLog(models.Model):
    """对照原版 ygg_log 表：接口访问审计。"""

    action = models.CharField("动作", max_length=32)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="yggdrasil_logs",
    )
    player = models.ForeignKey(
        "players.Player",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="yggdrasil_logs",
    )
    parameters = models.TextField("请求参数", default="", blank=True)
    ip = models.CharField("来源 IP", max_length=45, default="", blank=True)
    time = models.DateTimeField("时间", auto_now_add=True)

    class Meta:
        verbose_name = "Yggdrasil 日志"
        verbose_name_plural = verbose_name
        ordering = ["-time", "-id"]

    def __str__(self):
        return f"{self.action} @ {self.time}"
