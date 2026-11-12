"""players 应用：Minecraft 角色模型与 UUID 生成。"""

from django.db import models

from accounts.models import User

from .uuidgen import generate_uuid_by_option


class Player(models.Model):
    """对照 PHP 原版 players 表；uuid 列吸收原独立 uuid 表（schema 重设计）。"""

    pid = models.AutoField("PID", primary_key=True)
    uid = models.ForeignKey(
        User, on_delete=models.CASCADE, db_column="uid", related_name="players"
    )
    name = models.CharField("角色名", max_length=50, unique=True)
    uuid = models.CharField(
        "UUID", max_length=32, unique=True, default=generate_uuid_by_option, editable=False
    )
    tid_skin = models.ForeignKey(
        "textures.Texture",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="tid_skin",
        related_name="worn_as_skin",
    )
    tid_cape = models.ForeignKey(
        "textures.Texture",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="tid_cape",
        related_name="worn_as_cape",
    )
    last_modified = models.DateTimeField("最后修改", auto_now=True)

    class Meta:
        verbose_name = "角色"
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name

    @property
    def model(self) -> str:
        """材质模型：alex 皮肤为 slim，其余 default（对照原版 getModelAttribute）。"""
        if self.tid_skin and self.tid_skin.type == "alex":
            return "slim"
        return "default"

    def to_profile_dict(self) -> dict:
        """CustomSkinAPI R1 契约。

        对照原版 Player::toJson：
        {"username": name, "skins": {model: hash}, "cape": hash}
        无皮肤时 hash 为 None（原版 optional 语义）。
        """
        skin_hash = self.tid_skin.hash if self.tid_skin else None
        cape_hash = self.tid_cape.hash if self.tid_cape else None
        return {
            "username": self.name,
            "skins": {self.model: skin_hash},
            "cape": cape_hash,
        }
