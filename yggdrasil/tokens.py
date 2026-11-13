r"""Yggdrasil JWT 工具（对照原版 Lcobucci\JWT HMAC-SHA256 签发）。

签名密钥与 Django session/CSRF 的 SECRET_KEY 通过 HMAC 固定盐派生
隔离（见 _jwt_secret）；claims 对照原版（AuthController.php）。
"""

import hashlib
import hmac
import time

import jwt
from django.conf import settings

# 派生盐：域隔离标识，轮换密钥时改动此串即可强制全部令牌重新签发
_DERIVATION_SALT = b"blessing-skin:yggdrasil-jwt:v1"


def _jwt_secret() -> bytes:
    """JWT 签名密钥：HMAC(SECRET_KEY, 固定盐)。

    与 Django session/CSRF 签名用途相互隔离：一侧密钥泄露不直接给出
    另一侧（派生单向，JWT 密钥不可反推 SECRET_KEY）。SECRET_KEY 轮换
    或盐变更会使既有 accessToken 一次性失效，启动器重新 authenticate
    即恢复，无需人工干预。
    """
    return hmac.new(
        settings.SECRET_KEY.encode(), _DERIVATION_SALT, hashlib.sha256
    ).digest()


def issue_access_token(
    user_uuid: str, ygg_token_uuid: str, expires_in: int, spr: str = ""
) -> str:
    """签发 accessToken（HMAC-SHA256）。

    claims 对照原版（AuthController.php）：
    - iss: Yggdrasil-Auth
    - sub: 用户 UUID（邮箱派生）
    - yggt: 令牌唯一 ID（原版 refresh 轮换核心）
    - spr: 绑定角色 UUID（仅有绑定时携带；L63/L159/L171）
    - exp / iat
    """
    now = int(time.time())
    payload = {
        "iss": "Yggdrasil-Auth",
        "sub": user_uuid,
        "yggt": ygg_token_uuid,
        "iat": now,
        "exp": now + expires_in,
    }
    if spr:
        payload["spr"] = spr
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    """校验并解码；无效/过期返回 None。"""
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def user_uuid_from_email(email: str) -> str:
    """用户 UUID：uuid5(DNS namespace, email)，对照原版。"""
    import uuid

    return uuid.uuid5(uuid.NAMESPACE_DNS, email.lower()).hex
