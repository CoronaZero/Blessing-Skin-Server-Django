r"""Yggdrasil JWT 工具（对照原版 Lcobucci\JWT HMAC-SHA256 签发）。"""

import time

import jwt
from django.conf import settings


def issue_access_token(user_uuid: str, ygg_token_uuid: str, expires_in: int) -> str:
    """签发 accessToken（HMAC-SHA256，JWT_SECRET）。

    claims 对照原版：
    - iss: Yggdrasil-Auth
    - sub: 用户 UUID（邮箱派生）
    - yggt: 令牌唯一 ID（原版 refresh 轮换核心）
    - spr: 选中角色（可选）
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
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    """校验并解码；无效/过期返回 None。"""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def user_uuid_from_email(email: str) -> str:
    """用户 UUID：uuid5(DNS namespace, email)，对照原版。"""
    import uuid

    return uuid.uuid5(uuid.NAMESPACE_DNS, email.lower()).hex
