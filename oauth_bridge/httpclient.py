"""oauth_bridge 出站 HTTP 封装：统一代理与超时。

所有对 Microsoft / Mojang 的请求（OAuth 换 token、Xbox 链路、profile、
纹理下载、透传 hasJoined）必须经由本模块，保证：
- 走面板配置的出站代理（microsoft_outbound_proxy），留空直连；
- 不读环境变量代理（trust_env=False，生产行为可预期）；
- 测试可整体 monkeypatch outbound_get/outbound_post。

代理仅支持 http/https scheme（requests 无 pysocks 依赖，socks5 请由
代理软件转换为 http 端口后填写）。
"""

import requests

from options.models import Option

DEFAULT_TIMEOUT = 10  # 秒

# trust_env=False：不继承 HTTP(S)_PROXY 环境变量，代理只走面板配置
_session = requests.Session()
_session.trust_env = False


def _proxies() -> dict | None:
    raw = str(Option.objects.get_value("microsoft_outbound_proxy", "") or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    return {"http": raw, "https": raw}


def outbound_get(url, *, params=None, timeout=DEFAULT_TIMEOUT, headers=None):
    return _session.get(
        url, params=params, headers=headers, timeout=timeout, proxies=_proxies()
    )


def outbound_post(url, *, json=None, data=None, timeout=DEFAULT_TIMEOUT, headers=None):
    return _session.post(
        url, json=json, data=data, headers=headers, timeout=timeout,
        proxies=_proxies(),
    )
