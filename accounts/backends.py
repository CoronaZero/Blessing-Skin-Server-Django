"""自定义认证后端：email 或角色名登录 + legacy 密码桥接 + 登录后自动升级哈希。"""

from accounts.models import User
from players.models import Player


class EmailOrPlayerNameBackend:
    """对照原版：登录标识自动判别（email 格式 → 邮箱，否则视为角色名）。

    密码校验走 User.verify_password（多算法兼容层），
    成功后若散列非 Django/Argon2 格式自动升级（原版能力增强：免迁移升级）。
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        identification = username or kwargs.get("identification")
        if not identification or not password:
            return None

        user = self._find_user(identification)
        if user is None:
            # 恒定时间：对不存在的用户也执行一次哈希计算，防时序枚举
            from django.contrib.auth.hashers import make_password

            make_password("dummy-timing-equalizer")
            return None

        if not user.is_active:  # DOT validate_user 通道（is_active=False 拒绝）
            return None
        if user.permission == User.Permission.BANNED:
            return None
        if not user.verify_password(password):
            return None

        self._upgrade_hash_if_needed(user, password)
        return user

    @staticmethod
    def _find_user(identification: str) -> User | None:
        if "@" in identification:
            return User.objects.filter(email=identification).first()
        player = Player.objects.filter(name=identification).select_related("uid").first()
        return player.uid if player else None

    @staticmethod
    def _upgrade_hash_if_needed(user: User, raw_password: str) -> None:
        stored = user.password or ""
        django_formats = stored.startswith(("bcrypt$", "argon2$", "pbkdf2", "scrypt$"))
        if not django_formats:
            user.set_password(raw_password)
            user.save(update_fields=["password"])

    def get_user(self, uid):
        try:
            return User.objects.get(uid=uid)
        except User.DoesNotExist:
            return None
