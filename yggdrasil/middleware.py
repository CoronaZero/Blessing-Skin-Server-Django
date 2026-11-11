"""Yggdrasil 中间件：Content-Type 校验 / IP 节流 / Bearer 认证 / 体积上限。"""

import hashlib
import json

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse


def error_response(status: int, error: str, message: str, cause: str = "") -> HttpResponse:
    payload = {"error": error, "errorMessage": message}
    if cause:
        payload["cause"] = cause
    return HttpResponse(
        json.dumps(payload), status=status, content_type="application/json"
    )


class YggdrasilMiddleware:
    """统一挂 /api/yggdrasil/* 的中间件链。

    对照原版三个 middleware + 两项安全修复：
    - CheckContentType：仅 application/json（材质上传 PUT 除外，走 image/png）
    - Throttle：IP 维度（原版 username 键缺陷修复）
    - 请求体大小上限（原版无限制的内存耗尽修复）
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 仅处理 yggdrasil API 前缀
        if not request.path.startswith("/api/yggdrasil/"):
            return self.get_response(request)

        return self.get_response(request)


def check_content_type(request) -> HttpResponse | None:
    """authenticate/refresh 等端点必须 application/json。"""
    if request.method in ("POST", "PUT") and not request.path.endswith(
        ("/skin", "/cape")
    ):
        ctype = (request.content_type or "").split(";")[0].strip()
        if ctype != "application/json":
            return error_response(
                415, "IllegalArgumentException", "Content-Type 必须为 application/json"
            )
    return None


RATE_LIMIT_DEFAULT_SECONDS = 1.2  # 原版默认 1000ms


def throttle_by_ip(request) -> HttpResponse | None:
    """IP 维度节流（对照原版 Throttle，键从 username 改为 IP+端点）。

    原版缺陷（静态审查中危②）：username 是攻击者可控任意串，
    可构造无限缓存键撑爆存储；IP 键下伪造用户名不再产生新键。
    """
    seconds = getattr(
        settings, "YGG_RATE_LIMIT_SECONDS", RATE_LIMIT_DEFAULT_SECONDS
    )
    if seconds <= 0:
        return None

    ip = _client_ip(request)
    key = "ygg_throttle:" + hashlib.sha256(f"{ip}:{request.path}".encode()).hexdigest()
    now = time_ms()
    last = cache.get(key)
    if last is not None:
        retry_after = seconds * 1000 - (now - last)
        if retry_after > 0:
            resp = error_response(
                429, "ForbiddenOperationException",
                f"请求过于频繁，请 {round(retry_after / 1000, 1)} 秒后再试",
            )
            resp["Retry-After"] = str(int(retry_after // 1000) + 1)
            return resp
    cache.set(key, now, max(60, int(seconds) * 2))
    return None


MAX_BODY_DEFAULT = 1024 * 1024  # 1 MiB


def check_body_size(request) -> HttpResponse | None:
    """请求体大小上限（原版无上限，PUT 全量读内存的 DoS 修复）。"""
    limit = getattr(settings, "YGG_MAX_BODY_BYTES", MAX_BODY_DEFAULT)
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > limit:
        return error_response(
            413, "IllegalArgumentException", "请求体过大"
        )
    return None


def time_ms() -> int:
    import time

    return int(time.time() * 1000)


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def get_bearer_user(request):
    """Bearer accessToken → (user, token_row)；无效返回 (None, None)。

    对照原版 CheckBearerToken + Token::find（Cache 白名单语义改为 DB 持久化）。
    """
    from accounts.models import User

    from .models import YggdrasilToken
    from .tokens import decode_access_token

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, None
    access_token = auth_header[7:].strip()
    if not access_token:
        return None, None

    token_row = YggdrasilToken.objects.filter(access_token=access_token).first()
    if token_row is None or not token_row.is_valid():
        return None, None
    if decode_access_token(access_token) is None:
        return None, None
    user = User.objects.filter(uid=token_row.owner_id).first()
    if user is None or user.permission == User.Permission.BANNED:
        return None, None
    return user, token_row
