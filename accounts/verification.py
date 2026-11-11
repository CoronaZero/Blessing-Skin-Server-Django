"""邮箱验证令牌（对照原版签名 URL 语义）。

原版用 Laravel signed URL（HMAC）；Django 侧同构实现：
HMAC(uid + email, SECRET_KEY)。email 变更后旧链接自动失效。
"""

from django.core import signing

from accounts.models import User

# 有效期与原版一致（1 小时），带时间戳的 salted HMAC（TimestampSigner）
_TOKEN_SALT = "email-verification"


def make_verification_token(user: User) -> str:
    return signing.dumps({"uid": user.uid, "email": user.email}, salt=_TOKEN_SALT)


def resolve_verification_token(token: str) -> User | None:
    """令牌有效返回用户；过期/篡改/邮箱已变更返回 None。"""
    try:
        payload = signing.loads(token, salt=_TOKEN_SALT, max_age=3600)
    except (signing.BadSignature, signing.SignatureExpired):
        return None
    try:
        user = User.objects.get(uid=payload["uid"])
    except (User.DoesNotExist, KeyError):
        return None
    if user.email != payload.get("email"):  # 换邮箱后旧链接失效
        return None
    return user


def make_verification_link(user: User) -> str:
    from django.urls import reverse

    path = reverse("accounts:verify", args=[make_verification_token(user)])
    from django.contrib.sites.shortcuts import get_current_site

    return f"https://{get_current_site(None).domain}{path}"
