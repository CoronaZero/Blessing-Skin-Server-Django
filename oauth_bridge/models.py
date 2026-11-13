"""oauth_bridge 应用：第三方 OAuth 登录桥接与正版角色绑定。

设计要点（防账号接管）：
- Microsoft 登录按 sub 稳定标识绑定站内账号，email 仅作展示，不参与匹配；
- 正版角色绑定走 Microsoft → Xbox Live → XSTS → Minecraft profile 链路，
  绑定记录不创建 Player（只读角色，不占角色名空间）；
- 解绑冷却由记录表承载（超管可配天数）。
"""

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import User


class MsaIdentity(models.Model):
    """站内账号 ↔ Microsoft 账号（登录绑定）。

    sub 为 Azure AD v2 的稳定用户标识（对同一用户永不变化）；
    email/display_name 仅用于展示与建号参考，不参与账号匹配。
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="msa_identity", verbose_name="用户"
    )
    sub = models.CharField("Microsoft sub", max_length=64, unique=True)
    email = models.CharField("Microsoft 邮箱（展示）", max_length=100, default="")
    display_name = models.CharField("Microsoft 昵称（展示）", max_length=64, default="")
    created_at = models.DateTimeField("绑定时间", auto_now_add=True)

    class Meta:
        verbose_name = "Microsoft 账号绑定"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.user_id} ↔ msa:{self.sub[:12]}…"


class MojangBinding(models.Model):
    """站内账号 ↔ Minecraft 正版角色绑定（只读角色）。

    - mc_uuid 为 Mojang profile id（32 位无连字符小写 hex，全站唯一）；
    - 皮肤/披风纹理从 textures.minecraft.net 拉取后存入本地纹理存储：
      *_mojang_hash 记录上游 URL hash（变化检测），*_local_hash 为本地
      存储文件名（内容 sha256，与站内纹理存储一致），同步不占积分。
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="mojang_binding",
        verbose_name="用户",
    )
    mc_uuid = models.CharField("正版 UUID", max_length=32, unique=True)
    mc_name = models.CharField("正版角色名", max_length=50)
    skin_mojang_hash = models.CharField("上游皮肤 hash", max_length=64, default="")
    skin_local_hash = models.CharField("本地皮肤文件名", max_length=64, default="")
    skin_model = models.CharField("皮肤模型", max_length=8, default="default")
    cape_mojang_hash = models.CharField("上游披风 hash", max_length=64, default="")
    cape_local_hash = models.CharField("本地披风文件名", max_length=64, default="")
    textures_synced_at = models.DateTimeField("纹理同步时间", null=True, blank=True)
    created_at = models.DateTimeField("绑定时间", auto_now_add=True)

    class Meta:
        verbose_name = "正版角色绑定"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.user_id} ↔ mc:{self.mc_name}"


class UnbindCooldown(models.Model):
    """解绑冷却记录：绑定时按 (user, kind) 最新一条计算剩余冷却。"""

    class Kind(models.TextChoices):
        MSA = "msa", "Microsoft 账号"
        GENUINE = "genuine", "正版角色"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="oauth_unbind_records",
        verbose_name="用户",
    )
    kind = models.CharField("类型", max_length=10, choices=Kind.choices)
    unbound_at = models.DateTimeField("解绑时间", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "OAuth 解绑记录"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.user_id}:{self.kind}@{self.unbound_at:%Y-%m-%d}"

    @classmethod
    def record(cls, user, kind: str) -> "UnbindCooldown":
        """记录一次解绑（冷却期起点）。"""
        return cls.objects.create(user=user, kind=kind)

    @classmethod
    def remaining_seconds(cls, user, kind: str, days: int) -> int:
        """距离可再次绑定的剩余秒数；0 表示可绑定。

        days<=0 视为不冷却；无记录可绑定。
        """
        if days <= 0:
            return 0
        latest = (
            cls.objects.filter(user=user, kind=kind)
            .order_by("-unbound_at")
            .values_list("unbound_at", flat=True)
            .first()
        )
        if latest is None:
            return 0
        until = latest + timedelta(days=days)
        remaining = (until - timezone.now()).total_seconds()
        return max(0, int(remaining))
