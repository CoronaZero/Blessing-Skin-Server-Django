import os

from django.contrib.auth.hashers import Argon2PasswordHasher, BasePasswordHasher
from django.core.exceptions import ImproperlyConfigured


class LegacyHashMixin:
    """PHP 原版 BaseCipher 语义：无格式散列 + 全局 SALT。

    原版通过 PWD_METHOD/SALT 环境变量选择唯一算法；Django 版保留全部
    算法并按散列特征自动识别，散列值不区分大小写无关、恒为十六进制。
    """

    # 子类覆盖：输出十六进制长度（MD5=32, SHA256=64, SHA512=128）
    hex_length = 0
    # PHP 密码算法名（用于诊断信息）
    php_name = ""

    def __init__(self, salt: str = ""):
        self.salt = salt or _global_salt()

    def hash(self, password: str) -> str:
        raise NotImplementedError

    def verify(self, password: str, hashed: str) -> bool:
        import hmac

        return hmac.compare_digest(hashed.lower(), self.hash(password).lower())


class MD5Hasher(LegacyHashMixin):
    """原版 Cipher\\MD5：md5(password)。"""

    hex_length = 32
    php_name = "MD5"

    def hash(self, password: str) -> str:
        import hashlib

        return hashlib.md5(password.encode()).hexdigest()


class Salted2MD5Hasher(LegacyHashMixin):
    """原版 Cipher\\SALTED2MD5：md5(md5(password) + salt)。"""

    hex_length = 32
    php_name = "SALTED2MD5"

    def hash(self, password: str) -> str:
        import hashlib

        return hashlib.md5((MD5Hasher.hash(self, password) + self.salt).encode()).hexdigest()


class SHA256Hasher(LegacyHashMixin):
    """原版 Cipher\\SHA256：sha256(password)。"""

    hex_length = 64
    php_name = "SHA256"

    def hash(self, password: str) -> str:
        import hashlib

        return hashlib.sha256(password.encode()).hexdigest()


class Salted2SHA256Hasher(LegacyHashMixin):
    """原版 Cipher\\SALTED2SHA256：sha256(sha256(password) + salt)。"""

    hex_length = 64
    php_name = "SALTED2SHA256"

    def hash(self, password: str) -> str:
        import hashlib

        return hashlib.sha256(
            (SHA256Hasher.hash(self, password) + self.salt).encode()
        ).hexdigest()


class SHA512Hasher(LegacyHashMixin):
    """原版 Cipher\\SHA512：sha512(password)。"""

    hex_length = 128
    php_name = "SHA512"

    def hash(self, password: str) -> str:
        import hashlib

        return hashlib.sha512(password.encode()).hexdigest()


class Salted2SHA512Hasher(LegacyHashMixin):
    """原版 Cipher\\SALTED2SHA512：sha512(sha512(password) + salt)。"""

    hex_length = 128
    php_name = "SALTED2SHA512"

    def hash(self, password: str) -> str:
        import hashlib

        return hashlib.sha512(
            (SHA512Hasher.hash(self, password) + self.salt).encode()
        ).hexdigest()


def _global_salt() -> str:
    """全局 SALT（迁移自 PHP .env 同名变量），散列算法需要时读取。"""
    return os.environ.get("SALT", "")


# ---- 按散列特征识别 legacy 算法（供登录分派）----

# 十六进制长度 → hasher 类（长度相同的多算法并存时按序尝试）
_LENGTH_TABLE: list[tuple[int, list[type[LegacyHashMixin]]]] = [
    (32, [MD5Hasher, Salted2MD5Hasher]),
    (64, [SHA256Hasher, Salted2SHA256Hasher]),
    (128, [SHA512Hasher, Salted2SHA512Hasher]),
]


def identify_legacy_hash(hashed: str) -> list[type[LegacyHashMixin]]:
    """按长度返回候选 legacy hasher 列表；非 legacy 格式返回空。"""
    if not hashed or "$" in hashed:
        return []
    if len(hashed) not in (32, 64, 128):
        return []
    if not all(c in "0123456789abcdefABCDEF" for c in hashed):
        return []
    for length, candidates in _LENGTH_TABLE:
        if len(hashed) == length:
            return candidates
    return []


def normalize_php_prefix(hashed: str) -> str:
    """PHP bcrypt($2y$) 前缀归一化为 bcrypt 库识别的 $2b$。

    搬运历史数据时保持散列字节不动，仅校验前转换。
    注意：Django BCryptPasswordHasher 的存储格式为 ``bcrypt$<salt>$<hash>``
    封装，与 PHP 裸 ``$2y$`` 串不同构；对 PHP 历史散列的校验统一走
    :func:`verify_php_bcrypt`（bcrypt 库直校验），不走 Django hasher 链。
    """
    if hashed.startswith("$2y$"):
        return "$2b$" + hashed[4:]
    return hashed


def verify_php_bcrypt(password: str, hashed: str) -> bool:
    """校验 PHP password_hash 生成的裸 bcrypt 串（$2y$/$2a$/$2b$）。"""
    try:
        import bcrypt as _bcrypt

        return _bcrypt.checkpw(password.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def verify_php_argon2(password: str, hashed: str) -> bool:
    """校验 PHP password_hash 生成的裸 argon2 串（$argon2i$/$argon2id$）。

    与 Django Argon2PasswordHasher 的存储格式（argon2$<盐>$<哈希>）不同构，
    PHP 历史散列为 RFC 9106 裸格式，用 argon2-cffi 的底层 PasswordHasher 直校验。
    """
    try:
        import argon2.exceptions
        from argon2.low_level import Type, verify_secret

        type_map = {
            "$argon2i$": Type.I,
            "$argon2d$": Type.D,
            "$argon2id$": Type.ID,
        }
        for prefix, argon_type in type_map.items():
            if hashed.startswith(prefix):
                try:
                    verify_secret(hashed.encode(), password.encode(), argon_type)
                    return True
                except argon2.exceptions.VerifyMismatchError:
                    return False
                except (argon2.exceptions.VerificationError, argon2.exceptions.InvalidHashError):
                    return False
        return False
    except ImportError:
        return False


class PhpCompatPasswordHasher(Argon2PasswordHasher):
    """登录默认 hasher：Argon2（升级目标算法）。

    检验侧做 PHP 前缀归一化；encode 侧完全复用 Django 原生 Argon2。
    新密码一律用本 hasher 签发（原版 BCRYPT 算法族被 Argon2 取代，
    兼容性仅在校验方向保留）。
    """

    algorithm = "argon2"

    def verify(self, password, encoded):
        normalized = normalize_php_prefix(encoded)
        return super().verify(password, normalized)


class LegacyDjangoHasher(BasePasswordHasher):
    """把历史散列包装成 Django hasher 协议，供 authenticate() 分派。

    Django 的 identify_hasher 依据散列前缀分派，legacy 散列无前缀，
    因此提供一个带前缀标记的包装：存储时保持原样（迁移数据不改写），
    仅在登录校验时按特征识别。该 hasher 不用于签发新密码。
    """

    algorithm = "legacy"

    def salt(self):
        raise ImproperlyConfigured("legacy hasher 不用于签发新密码")

    def encode(self, password, salt):
        raise ImproperlyConfigured("legacy hasher 不用于签发新密码")

    def verify(self, password, encoded):
        candidates = identify_legacy_hash(encoded)
        if candidates:
            return any(h().verify(password, encoded) for h in candidates)
        if encoded.startswith("$2"):
            return verify_php_bcrypt(password, encoded)
        if encoded.startswith("$argon2"):
            return verify_php_argon2(password, encoded)
        return False

    def must_update(self, encoded):
        return True  # 所有无格式散列登录成功后都应升级

    def harden_runtime(self, password, encoded):
        pass
