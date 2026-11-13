"""oauth_bridge OAuth state 管理：cache 一次性令牌（防 CSRF）。"""

import secrets

from django.core.cache import cache

STATE_TTL = 600  # 排程到回调的最大容忍时长（秒）

_PREFIX = "msa_oauth_state:"


def put_state(payload: dict) -> str:
    """生成随机 state 并挂载荷；state 值即 cache 键（不可猜测、不可枚举）。"""
    token = secrets.token_urlsafe(24)
    cache.set(_PREFIX + token, payload, STATE_TTL)
    return token


def pop_state(state: str) -> dict | None:
    """取回并立即作废（一次性）；无效/过期返回 None。"""
    if not state or len(state) > 128:
        return None
    key = _PREFIX + state
    payload = cache.get(key)
    if payload is not None:
        cache.delete(key)
    return payload
