"""accounts 应用：自定义用户模型与多算法密码兼容层。

对照 PHP 原版 users 表与 Cipher 家族（10 种算法）。
"""

import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from .hashers import (
    identify_legacy_hash,
    verify_php_argon2,
    verify_php_bcrypt,
)


class UserManager(BaseUserManager):
    """email 即用户名（对照原版登录标识 semantcs：邮箱或角色名）。"""

    use_in_migrations = True

    def _create_user(self, email, nickname, password, **extra_fields):
        if not email:
            raise ValueError("必须提供 email")
        email = self.normalize_email(email)
        user = self.model(email=email, nickname=nickname or "", **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, nickname="", password=None, **extra_fields):
        extra_fields.setdefault("permission", User.Permission.NORMAL)
        return self._create_user(email, nickname, password, **extra_fields)

    def create_superuser(self, email, nickname="", password=None, **extra_fields):
        extra_fields.setdefault("permission", User.Permission.SUPER_ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._create_user(email, nickname, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """对照 PHP 原版 users 表。

    字段重设计：去除 authme 遗留（realname/username/regdate），
    新增 oauth_provider（Phase 7 OAuth 绑定表的前置字段）。
    """

    class Permission(models.IntegerChoices):
        BANNED = -1, "封禁"
        NORMAL = 0, "普通用户"
        ADMIN = 1, "管理员"
        SUPER_ADMIN = 2, "超级管理员"

    uid = models.AutoField("UID", primary_key=True)
    email = models.EmailField("邮箱", unique=True, max_length=100)
    nickname = models.CharField("昵称", max_length=50, default="")
    password = models.CharField("密码散列", max_length=255)
    score = models.IntegerField("积分", default=0)
    avatar = models.IntegerField("头像纹理 ID", default=0)
    ip = models.CharField("注册 IP", max_length=45, default="")
    permission = models.IntegerField(
        "权限", choices=Permission.choices, default=Permission.NORMAL
    )
    verified = models.BooleanField("邮箱已验证", default=False)
    locale = models.CharField("语言偏好", max_length=10, default="")
    is_dark_mode = models.BooleanField("暗色模式", default=False)
    oauth_provider = models.CharField("OAuth 提供者", max_length=32, default="", blank=True)
    last_sign_at = models.DateTimeField("最后签到", null=True, blank=True)
    register_at = models.DateTimeField("注册时间", default=timezone.now)

    # Django auth 体系字段：is_active 持久化（BANNED 派生语义）；
    # is_staff 独立控制 Admin 访问（superuser 由 permission=SUPER_ADMIN 对应）
    is_active = models.BooleanField("启用", default=True)
    is_staff = models.BooleanField("后台访问", default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["nickname"]

    class Meta:
        verbose_name = "用户"
        verbose_name_plural = verbose_name

    def is_admin(self) -> bool:
        return self.permission >= self.Permission.ADMIN

    def verify_password(self, raw: str) -> bool:
        """多算法校验，对照原版 HasPassword::verifyPassword。

        分派顺序：
        1. Django 原生格式（bcrypt$/argon2$/pbkdf2$ 封装）→ Django hasher 链
        2. PHP 裸 bcrypt（$2y$/$2a$/$2b$）→ bcrypt 库直校验
        3. PHP 裸 argon2（$argon2i$ 等）→ argon2-cffi 直校验
        4. 无格式 legacy 散列（MD5/SHA 系 32/64/128 hex）→ 按长度尝试候选
        """
        stored = self.password
        if not stored:
            return False
        # Django 封装格式：算法名开头（bcrypt$/argon2$/pbkdf2_sha256$...）
        head = stored.split("$", 1)[0]
        if head in ("bcrypt", "argon2", "pbkdf2_sha256", "pbkdf2_sha1", "scrypt"):
            return check_password(raw, stored)
        if stored.startswith("$2"):
            return verify_php_bcrypt(raw, stored)
        if stored.startswith("$argon2"):
            return verify_php_argon2(raw, stored)
        candidates = identify_legacy_hash(stored)
        if candidates:
            return any(h().verify(raw, stored) for h in candidates)
        return False

    def change_password(self, raw: str) -> None:
        """对照原版 changePassword：新密码统一用 Django Argon2 签发。"""
        self.set_password(raw)

    def get_full_name(self):
        return self.nickname or self.email

    def get_short_name(self):
        return self.nickname or self.email

    def __str__(self):
        return f"{self.nickname} <{self.email}>" if self.nickname else self.email

    @property
    def player_name(self) -> str:
        """首个角色名（对照原版 player_name accessor）；无角色时为空。

        players 反向关系未建立（players app 模型缺失/未迁移）时安全返回空。
        """
        try:
            first = self.players.first()
        except (AttributeError, Exception):
            return ""
        return first.name if first else ""


class PasswordReset(models.Model):
    """一次性密码重置令牌（安全修复③：替代原版可复用的签名 URL）。

    - DB 存 token 哈希，泄露库 dump 无法构造有效链接
    - used_at 落库后即失效，不可重复使用
    - expires_at：有效期与原版一致（1 小时）
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_resets")
    token_hash = models.CharField("令牌哈希", max_length=64, unique=True)
    # 明文 token 仅在创建响应链路中暂存（发邮件），不落库
    token = None
    expires_at = models.DateTimeField("过期时间")
    used_at = models.DateTimeField("使用时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "密码重置令牌"
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"reset-token({self.user_id})"

    @classmethod
    def issue(cls, user: "User") -> "PasswordReset":
        """签发新令牌（旧令牌全部作废）。"""
        cls.objects.filter(user=user).delete()
        raw = secrets.token_urlsafe(32)
        reset = cls.objects.create(
            user=user,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(hours=1),
        )
        reset.token = raw  # 仅内存暂存，供邮件模板使用
        return reset

    @classmethod
    def consume(cls, user: "User", token: str) -> bool:
        """核销令牌：有效且未用未过期 → 标记并返回 True。"""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        reset = cls.objects.filter(user=user, token_hash=token_hash).first()
        if reset is None or reset.used_at is not None:
            return False
        if timezone.now() > reset.expires_at:
            reset.delete()
            return False
        reset.used_at = timezone.now()
        reset.save(update_fields=["used_at"])
        return True

    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at
