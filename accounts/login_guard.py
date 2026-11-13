"""登录失败锁定：IP 维度失败计数与硬锁定。

对照 PHP 原版 login_fails_<ip> 计数（原版 >3 次后强制验证码）；本项目
验证码 provider 启用时已无条件强制（更强），未启用时按计数硬锁定——
否则无验证码站点 /auth/login 与 MSA 引导绑定可无限撞库。
"""

import hashlib

from django.core.cache import cache

from .captcha import get_provider

LOCK_THRESHOLD = 5  # 未启用验证码时的硬锁定阈值（次）
TTL_SECONDS = 3600  # 计数窗口（与原版一致）


def _key(ip: str) -> str:
    # 沿用原 key 前缀（login_fails:<sha256(ip)>），历史部署数据语义连续
    return "login_fails:" + hashlib.sha256(ip.encode()).hexdigest()


def is_locked(ip: str) -> bool:
    """是否锁定：provider 启用时由验证码承担人机校验（原版 >3 次强制
    验证码的语义），未启用时按失败计数硬锁。"""
    if get_provider():
        return False
    return (cache.get(_key(ip), 0) or 0) >= LOCK_THRESHOLD


def record_failure(ip: str) -> None:
    """记一次失败（不存在用户与密码错误一并计数，防账号枚举探测）。"""
    cache.set(_key(ip), (cache.get(_key(ip), 0) or 0) + 1, TTL_SECONDS)


def clear(ip: str) -> None:
    """登录成功清零。"""
    cache.delete(_key(ip))
