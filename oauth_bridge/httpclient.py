"""oauth_bridge 出站 HTTP 封装：统一代理、UA、超时与网络级重试。

所有对 Microsoft / Mojang 的请求（OAuth 换 token、Xbox 链路、profile、
纹理下载、透传 hasJoined）必须经由本模块，保证：
- 走面板配置的出站代理（microsoft_outbound_proxy），留空直连；
- 不读环境变量代理（trust_env=False，生产行为可预期）；
- **显式 User-Agent**：requests 默认 UA（python-requests/*）是爬虫滥用
  大户，微软服务端风控会拒绝——社区参考实现（minecraft-launcher-lib）对
  每个微软请求都带自定义 UA，此处同样自报家门；JSON 端点同时显式
  Accept: application/json（HMCL / minecraft-launcher-lib 同款行为）；
- 网络异常（requests.RequestException）按 retries 参数重试——微软认证
  端点存在间歇性失败（HMCL 对全链路 retry(5) 即为此）；HTTP 非 2xx 为
  确定性错误，不重试、交由调用方按响应体处理；
- 测试可整体 monkeypatch outbound_get/outbound_post。

代理仅支持 http/https scheme（requests 无 pysocks 依赖，socks5 请由
代理软件转换为 http 端口后填写）。
"""

import requests

from options.models import Option

DEFAULT_TIMEOUT = 10  # 秒
DEFAULT_RETRIES = 2  # 网络级重试次数（仅 RequestException）

USER_AGENT = "blessing-skin-server/0.1 (+https://skin.dreamweaver.top)"
JSON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}

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


def _merged_headers(headers: dict | None) -> dict:
    merged = dict(JSON_HEADERS)
    if headers:
        merged.update(headers)
    return merged


def outbound_get(url, *, params=None, timeout=DEFAULT_TIMEOUT, headers=None,
                 retries=DEFAULT_RETRIES):
    last_exc: Exception | None = None
    for _attempt in range(max(1, retries)):
        try:
            return _session.get(
                url, params=params, headers=_merged_headers(headers),
                timeout=timeout, proxies=_proxies(),
            )
        except requests.RequestException as exc:  # 网络级失败才重试
            last_exc = exc
    raise last_exc  # pragma: no cover - 防御：循环至少执行一次


def outbound_post(url, *, json=None, data=None, timeout=DEFAULT_TIMEOUT,
                  headers=None, retries=DEFAULT_RETRIES):
    last_exc: Exception | None = None
    for _attempt in range(max(1, retries)):
        try:
            return _session.post(
                url, json=json, data=data, headers=_merged_headers(headers),
                timeout=timeout, proxies=_proxies(),
            )
        except requests.RequestException as exc:  # 网络级失败才重试
            last_exc = exc
    raise last_exc  # pragma: no cover - 防御：循环至少执行一次
