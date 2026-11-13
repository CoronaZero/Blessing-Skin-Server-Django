"""oauth_bridge OAuth state 管理：cache 一次性令牌 + 发起会话绑定。

防授权码注入（OAuth code injection）：state 除一次性外，还须与发起
授权的浏览器会话一致——否则攻击者可用自己完成的 code+state 链接诱导
已登录受害者点击，把攻击者的 Microsoft 身份绑进受害者账号（bind 意图）
或让受害者登录进攻击者账号（login 意图）。回调时 state 与本会话发起
记录不一致，一律拒绝。
"""

import secrets

from django.core.cache import cache

STATE_TTL = 600  # 排程到回调的最大容忍时长（秒）

_PREFIX = "msa_oauth_state:"
# state 在发起会话中的落点：发起时写入，回调时比对后弹出
SESSION_KEY = "msa_oauth_state"


def put_state(payload: dict, request) -> str:
    """生成随机 state 挂载荷并记入发起会话；state 值即 cache 键（不可猜测）。"""
    token = secrets.token_urlsafe(24)
    cache.set(_PREFIX + token, payload, STATE_TTL)
    request.session[SESSION_KEY] = token
    return token


def pop_state(state: str, request) -> dict | None:
    """校验会话一致性并取回载荷（一次性）；不一致/无效/过期返回 None。

    会话记录在返回前无条件弹出（无论校验成败均作废，不给重试留口）。
    """
    if not state or len(state) > 128:
        return None
    expected = request.session.pop(SESSION_KEY, "")
    if not expected or expected != state:
        return None  # 非本会话发起（授权码注入）或未发起过
    key = _PREFIX + state
    payload = cache.get(key)
    if payload is not None:
        cache.delete(key)
    return payload
