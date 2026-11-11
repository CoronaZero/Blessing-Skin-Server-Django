"""accounts 模型测试：自定义 User 与多算法密码兼容层（TDD 先行）。

对照 PHP 原版：
- users 表语义：email/nickname/score/avatar/password/ip/permission(-1/0/1/2)/
  last_sign_at/register_at/verified/locale/is_dark_mode/oauth_provider
- 密码体系：PWD_METHOD（默认 BCRYPT）+ SALT 环境变量驱动的 10 种算法，
  Django 版做成 hasher 注册表 + 按格式自动识别（超越原版：无需全局配置
  即可校验任意历史算法，登录成功后自动升级为 Argon2）
- 已知向量来自对原版 BaseCipher 各实现的逐行复刻。
"""

import hashlib

import pytest

from accounts.hashers import (
    MD5Hasher,
    Salted2MD5Hasher,
    Salted2SHA256Hasher,
    Salted2SHA512Hasher,
    SHA256Hasher,
    SHA512Hasher,
    identify_legacy_hash,
    normalize_php_prefix,
    verify_php_argon2,
    verify_php_bcrypt,
)
from accounts.models import User

SALT = "test-salt-42"

# ---- 已知向量（与 PHP 原版实现逐字节一致）----
PLAIN = "my-secret-password"


def php_md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


def php_salted2md5(value: str, salt: str) -> str:
    return hashlib.md5((php_md5(value) + salt).encode()).hexdigest()


def php_salted2sha256(value: str, salt: str) -> str:
    return hashlib.sha256((hashlib.sha256(value.encode()).hexdigest() + salt).encode()).hexdigest()


def php_salted2sha512(value: str, salt: str) -> str:
    return hashlib.sha512((hashlib.sha512(value.encode()).hexdigest() + salt).encode()).hexdigest()


class TestUserModel:
    def test_create_user_with_fields(self, db):
        user = User.objects.create_user(
            email="steve@example.com",
            nickname="Steve",
            password="irrelevant",
        )
        assert user.uid is not None
        assert user.email == "steve@example.com"
        assert user.nickname == "Steve"
        assert user.score == 0
        assert user.permission == User.Permission.NORMAL
        assert user.verified is False

    def test_create_superuser_permissions(self, db):
        admin = User.objects.create_superuser(
            email="op@example.com", nickname="OP", password="irrelevant"
        )
        assert admin.permission == User.Permission.SUPER_ADMIN
        assert admin.is_staff is True
        assert admin.is_superuser is True

    def test_email_is_username_field(self):
        assert User.USERNAME_FIELD == "email"

    def test_permission_constants_match_php(self):
        """permission 常量对照 PHP 原版：-1 封禁 / 0 普通 / 1 管理员 / 2 超管。"""
        assert User.Permission.BANNED == -1
        assert User.Permission.NORMAL == 0
        assert User.Permission.ADMIN == 1
        assert User.Permission.SUPER_ADMIN == 2

    def test_is_admin_covers_admin_and_super(self, db):
        user = User.objects.create_user(email="a@x.com", nickname="a", password="x")
        assert user.is_admin() is False
        user.permission = User.Permission.ADMIN
        assert user.is_admin() is True
        user.permission = User.Permission.SUPER_ADMIN
        assert user.is_admin() is True

    def test_banned_user_excluded_from_all_users(self, db):
        User.objects.create_user(email="n@x.com", nickname="n", password="x")
        banned = User.objects.create_user(email="b@x.com", nickname="b", password="x")
        banned.permission = User.Permission.BANNED
        banned.save()
        emails = set(User.objects.all().values_list("email", flat=True))
        assert {"n@x.com", "b@x.com"} <= emails  # 普通管理器不过滤封禁
        assert User.objects.exclude(permission=User.Permission.BANNED).count() == 1

    def test_player_name_property(self, db):
        user = User.objects.create_user(email="p@x.com", nickname="p", password="x")
        assert user.player_name == ""
        # players 模型在后续功能中创建，此处验证空态行为


class TestLegacyHashers:
    """六种无格式散列的兼容（PHP 时代历史用户）。"""

    def test_md5_known_vector(self):
        assert MD5Hasher(SALT).hash(PLAIN) == php_md5(PLAIN)

    def test_salted2md5_uses_injected_salt(self):
        assert Salted2MD5Hasher(SALT).hash(PLAIN) == php_salted2md5(PLAIN, SALT)

    def test_salted2md5_ignores_salt_argument_of_plain_md5(self):
        """SALTED2MD5 只用 salt 于外层；内层仍是纯 md5。"""
        assert Salted2MD5Hasher("other").hash(PLAIN) == php_salted2md5(PLAIN, "other")

    def test_salted2sha256_known_vector(self):
        assert Salted2SHA256Hasher(SALT).hash(PLAIN) == php_salted2sha256(PLAIN, SALT)

    def test_salted2sha512_known_vector(self):
        assert Salted2SHA512Hasher(SALT).hash(PLAIN) == php_salted2sha512(PLAIN, SALT)

    def test_sha256_known_vector(self):
        assert SHA256Hasher(SALT).hash(PLAIN) == SHA256Hasher(SALT).hash(PLAIN)
        assert SHA256Hasher(SALT).hash(PLAIN) == hashlib.sha256(PLAIN.encode()).hexdigest()

    def test_sha512_known_vector(self):
        assert SHA512Hasher(SALT).hash(PLAIN) == hashlib.sha512(PLAIN.encode()).hexdigest()

    def test_verify_matches_hash(self):
        h = Salted2SHA256Hasher(SALT).hash(PLAIN)
        assert Salted2SHA256Hasher(SALT).verify(PLAIN, h) is True
        assert Salted2SHA256Hasher(SALT).verify("wrong", h) is False

    def test_identify_legacy_hash(self):
        assert identify_legacy_hash(php_md5(PLAIN)) == [MD5Hasher, Salted2MD5Hasher]
        assert identify_legacy_hash(php_salted2md5(PLAIN, SALT)) == [
            MD5Hasher,
            Salted2MD5Hasher,
        ]
        assert identify_legacy_hash("argon2$abc") == []  # 带格式标记的不走 legacy
        assert identify_legacy_hash("short") == []
        assert identify_legacy_hash("") == []
        assert identify_legacy_hash("z" * 32) == []  # 非十六进制


class TestDjangoNativeHashers:
    """原版 BCRYPT / ARGON2I / PHP_PASSWORD_HASH 的 Django 等价。

    PHP password_hash 输出 $2y$（bcrypt）与 $argon2i$ 前缀，
    Django hasher 识别 $2b$/$argon2$——需要前缀归一化桥接。
    向量生成：bcrypt.checkpw 由本测试包自带的 django hasher 链生成后
    经前缀改写为 $2y$，等价于 PHP password_hash(PASSWORD_BCRYPT) 的输出格式。
    """

    def _php_style_bcrypt(self, password: str) -> str:
        """用 bcrypt 库直接签发 PHP password_hash 同构格式（$2y$ 前缀）。"""
        import bcrypt as _bcrypt

        return _bcrypt.hashpw(
            password.encode(), _bcrypt.gensalt(rounds=10, prefix=b"2b")
        ).decode().replace("$2b$", "$2y$", 1)

    def test_bcrypt_2y_prefix_accepted(self):
        """PHP 裸 $2y$ bcrypt 通过 verify_php_bcrypt 直校验。"""
        php_bcrypt = self._php_style_bcrypt(PLAIN)
        assert php_bcrypt.startswith("$2y$")
        assert verify_php_bcrypt(PLAIN, php_bcrypt) is True
        assert verify_php_bcrypt("wrong", php_bcrypt) is False

    def test_bcrypt_2a_2b_accepted(self):
        import bcrypt as _bcrypt

        for prefix in (b"2a", b"2b"):
            hashed = _bcrypt.hashpw(
                PLAIN.encode(), _bcrypt.gensalt(rounds=10, prefix=prefix)
            ).decode()
            assert verify_php_bcrypt(PLAIN, hashed) is True

    def test_bcrypt_malformed_rejected(self):
        assert verify_php_bcrypt(PLAIN, "$2y$not-a-valid-hash") is False
        assert verify_php_bcrypt(PLAIN, "") is False

    def test_normalize_php_prefix(self):
        assert normalize_php_prefix("$2y$10$abc").startswith("$2b$")
        assert normalize_php_prefix("$2b$10$abc") == "$2b$10$abc"
        assert normalize_php_prefix("plain") == "plain"

    def test_php_argon2_accepted(self):
        """PHP password_hash Argon2i 输出的裸 $argon2i/$argon2id$ 可校验。"""
        import argon2

        hashed = argon2.PasswordHasher().hash(PLAIN)  # $argon2id$
        assert hashed.startswith("$argon2")
        assert verify_php_argon2(PLAIN, hashed) is True
        assert verify_php_argon2("wrong", hashed) is False

    def test_php_argon2_malformed_rejected(self):
        assert verify_php_argon2(PLAIN, "$argon2i$garbage") is False
        assert verify_php_argon2(PLAIN, "no-prefix") is False


class TestSalterEnv:
    """盐值环境变量的全局读取行为。"""

    def test_salted_hasher_reads_global_salt(self, monkeypatch):
        monkeypatch.setenv("SALT", "env-salt")
        assert Salted2SHA256Hasher().salt == "env-salt"

    def test_salted_hasher_defaults_to_empty(self, monkeypatch):
        monkeypatch.delenv("SALT", raising=False)
        assert Salted2SHA256Hasher().salt == ""


class TestHashMigration:
    """登录成功后自动升级到 Argon2（登录路径在 Phase 2 联测）。"""

    def test_model_verify_accepts_php_bcrypt_2y(self, db):
        """PHP $2y$ bcrypt 历史用户可通过模型层校验。"""
        import bcrypt as _bcrypt

        user = User.objects.create_user(
            email="legacy@x.com", nickname="legacy", password=None
        )
        user.password = _bcrypt.hashpw(
            PLAIN.encode(), _bcrypt.gensalt(rounds=10, prefix=b"2b")
        ).decode().replace("$2b$", "$2y$", 1)
        user.save()
        assert user.verify_password(PLAIN) is True
        assert user.verify_password("wrong") is False

    def test_model_verify_accepts_legacy_md5(self, db):
        user = User.objects.create_user(
            email="md5@x.com", nickname="md5user", password=None
        )
        user.password = php_md5(PLAIN)
        user.save()
        assert user.verify_password(PLAIN) is True

    def test_model_verify_accepts_salted2sha256(self, db, monkeypatch):
        """盐化散列依赖 PHP .env 的 SALT：校验侧读取同名环境变量。"""
        monkeypatch.setenv("SALT", SALT)
        user = User.objects.create_user(
            email="s2@x.com", nickname="s2user", password=None
        )
        user.password = php_salted2sha256(PLAIN, SALT)
        user.save()
        assert user.verify_password(PLAIN) is True
        assert user.verify_password("wrong") is False

    def test_change_password_issues_argon2(self, db):
        user = User.objects.create_user(
            email="chg@x.com", nickname="chg", password=None
        )
        user.password = php_md5(PLAIN)
        user.save()
        user.change_password("brand-new-pass")
        user.save()
        assert user.password.startswith("argon2$")
        assert user.verify_password("brand-new-pass") is True


class TestToString:
    def test_str_shows_email(self, db):
        user = User.objects.create_user(email="me@x.com", nickname="me", password="x")
        assert "me@x.com" in str(user)


@pytest.mark.django_db
class TestUserProfile:
    def test_locale_field_default(self):
        user = User.objects.create_user(email="l@x.com", nickname="l", password="x")
        assert user.locale == ""
        assert user.is_dark_mode is False

    def test_oauth_provider_field(self):
        user = User.objects.create_user(email="o@x.com", nickname="o", password="x")
        assert user.oauth_provider == ""
