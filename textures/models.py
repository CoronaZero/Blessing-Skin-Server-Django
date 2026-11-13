"""textures 应用：皮肤/披风纹理与用户衣柜。"""

from django.conf import settings
from django.db import models


class Texture(models.Model):
    """对照 PHP 原版 textures 表；新增 description（内嵌 texture-description）。

    文件存储以 hash 命名（Phase 3 存储后端），hash 全局唯一 →
    从 PHP 版迁移时皮肤文件零搬动。
    """

    class Type(models.TextChoices):
        STEVE = "steve", "Steve 皮肤"
        ALEX = "alex", "Alex 皮肤"
        CAPE = "cape", "披风"

    tid = models.AutoField("TID", primary_key=True)
    name = models.CharField("名称", max_length=50)
    type = models.CharField("类型", max_length=10, choices=Type.choices)
    hash = models.CharField("SHA-256", max_length=64, unique=True)
    size = models.IntegerField("大小(KB)", default=0)
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="uploads",
    )
    public = models.BooleanField("公开", default=True)
    likes = models.IntegerField("收藏数", default=0)
    description = models.TextField("描述", default="", blank=True)
    # 站点导入纹理的原始链接（入队前已剔追踪参数；用户上传为空）
    source_url = models.CharField("来源链接", max_length=500, default="", blank=True)
    upload_at = models.DateTimeField("上传时间", auto_now_add=True)

    class Meta:
        verbose_name = "纹理"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.name} ({self.type})"

    @property
    def model(self) -> str:
        """材质模型：alex → slim，其余 default（对照原版 getModelAttribute）。"""
        return "slim" if self.type == "alex" else "default"

    @property
    def likers(self):
        """收藏该纹理的用户集合（对照原版 likers belongsToMany 关系）。"""
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        return user_model.objects.filter(closet__texture=self)

    def delete_with_cleanup(self) -> None:
        """删除纹理并清理全部引用（佩戴角色/衣柜/举报）。

        对照原版 SkinlibController@delete + 本项目面板删除语义：
        - 角色佩戴置空（皮肤/披风任一引用该纹理）
        - 衣柜与举报级联清理
        - 同 hash 无其他行时连带删除物理文件
        """
        from players.models import Player

        from .storage import texture_storage

        Player.objects.filter(tid_skin=self).update(tid_skin=None)
        Player.objects.filter(tid_cape=self).update(tid_cape=None)
        ClosetItem.objects.filter(texture=self).delete()
        from moderation.models import Report

        Report.objects.filter(texture=self).delete()
        hash_value = self.hash
        self.delete()
        if not Texture.objects.filter(hash=hash_value).exists():
            try:
                texture_storage.delete(hash_value)
            except Exception:  # noqa: BLE001 - 文件已缺失时静默（与面板语义一致）
                pass


class ClosetItem(models.Model):
    """衣柜（对照原版 user_closet 中间表，重设计为显式模型）。

    同一用户对同一纹理仅一条记录（原表无唯一约束，行为上等价）。
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="closet",
    )
    texture = models.ForeignKey(
        Texture,
        on_delete=models.CASCADE,
        related_name="closet_items",
    )
    item_name = models.CharField("备注名", max_length=255, default="", blank=True)
    added_at = models.DateTimeField("加入时间", auto_now_add=True)

    class Meta:
        verbose_name = "衣柜项"
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["owner", "texture"], name="unique_closet_item"),
        ]

    def __str__(self):
        return f"{self.owner_id} → {self.texture_id}"
