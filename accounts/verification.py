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


def get_site_base_url() -> str:
    """站点对外根地址：优先 Option site_url，缺省 http://localhost:8000。

    原版对应 option('site_url')（BungeeCord 场景 forceRootUrl 同源）。
    """
    from options.models import Option

    configured = (Option.objects.get_value("site_url") or "").strip().rstrip("/")
    if configured:
        if not configured.startswith(("http://", "https://")):
            configured = "https://" + configured
        return configured
    return "http://localhost:8000"


def make_verification_link(user: User) -> str:
    from django.urls import reverse

    path = reverse("accounts:verify", args=[make_verification_token(user)])
    return get_site_base_url() + path
