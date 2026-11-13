"""站外纹理拉取：URL 净化与解析、SSRF 防护、HTTP 抓取。

对照 py-spider/skinlib_spider.py 的单件流程（忽略批量）：
  /skinlib/show/<tid>（或 ?tid=，兼容本站风格）→
  GET {site}/skinlib/info/{tid}（JSON 元数据）→
  GET {site}/textures/{hash}（PNG 文件）
"""

import ipaddress
import json
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests as http_client

# 追踪参数黑名单：utm_* 全族 + 常见分享/统计参数
_TRACKING_PARAM_EXACT = {
    "utm", "spm", "fbclid", "gclid", "msclkid", "igshid", "si",
    "from", "ref", "ref_src", "share_source", "share_medium", "share_token",
}

DEFAULT_USER_AGENT = "BlessingSkin-Django/1.0 (texture-import; single fetch)"

MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024  # 与上传体积上限一致
REQUEST_TIMEOUT = 30
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """拉取业务异常（面向用户的中文信息）。"""


# ---- URL 净化与解析（入队前调用）----


def sanitize_url(raw: str) -> str:
    """剥追踪参数，保留有效查询串；规范化 scheme/netloc。

    纯本地字符串操作，不发网络请求。
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw
    parts = urlsplit(raw)
    if not parts.netloc:
        return ""
    cleaned_q = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not (
            k.lower() in _TRACKING_PARAM_EXACT
            or k.lower().startswith("utm_")
        )
    ]
    return urlunsplit((
        parts.scheme.lower() or "https",
        parts.netloc,
        parts.path or "/",
        urlencode(cleaned_q),
        "",  # 丢弃锚点
    ))


def parse_target(sanitized_url: str) -> tuple[str, int]:
    """解析净化后的 URL → (站点根地址, 来源 TID)。

    支持两种形态：
      https://site/skinlib/show/929613（PHP 原版路由）
      https://site/skinlib/show?tid=87（本站查询参数风格）
    """
    parts = urlsplit(sanitized_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise FetchError("链接须为 http/https 的皮肤详情页地址")
    tid = None
    m = re.search(r"/skinlib/show/(\d+)", parts.path)
    if m:
        tid = int(m.group(1))
    else:
        for k, v in parse_qsl(parts.query):
            if k == "tid" and v.isdigit():
                tid = int(v)
                break
    if tid is None or tid <= 0:
        raise FetchError(
            "链接中未找到纹理编号（须形如 /skinlib/show/<TID> 或 /skinlib/show?tid=<TID>）"
        )
    site = urlunsplit((parts.scheme, parts.netloc, "", "", "")).rstrip("/")
    return site, tid


def assert_safe_host(host: str) -> None:
    """SSRF 防护：禁止解析到回环/内网/保留地址。"""
    if not host:
        raise FetchError("无效的主机地址")
    hostname = host.split(":")[0].strip("[]")  # 剥端口、IPv6 括号
    # 域名解析（失败则拒绝）
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise FetchError(f"无法解析主机：{hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            raise FetchError("不允许拉取内网或本机地址")


# ---- HTTP 拉取 ----


def _get(url: str, user_agent: str, tag: str) -> http_client.Response:
    """带重试的 GET；不可重试状态或耗尽后抛 FetchError。"""
    last_error = None
    for _attempt in range(3):
        try:
            resp = http_client.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": user_agent},
                allow_redirects=True,
            )
            if resp.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}"
                continue
            return resp
        except http_client.RequestException as exc:
            last_error = exc
    raise FetchError(f"{tag}请求失败（已重试）：{last_error}")


def fetch_texture(site: str, tid: int, user_agent: str) -> dict:
    """拉取单件纹理：元数据 + PNG 内容。

    返回 {"name", "type", "hash", "size_kb", "png": bytes}。
    """
    info_resp = _get(f"{site}/skinlib/info/{tid}", user_agent, "元数据")
    if info_resp.status_code in (403, 404):
        raise FetchError("来源站返回纹理不存在或非公开")
    if info_resp.status_code != 200:
        raise FetchError(f"元数据接口返回 HTTP {info_resp.status_code}")
    try:
        info = json.loads(info_resp.content)
    except ValueError as exc:
        raise FetchError("来源站元数据响应不是 JSON") from exc
    if not isinstance(info, dict) or not info.get("hash"):
        raise FetchError("来源站元数据缺少 hash 字段")
    if info.get("type") not in ("steve", "alex", "cape"):
        raise FetchError(f"未知纹理类型：{info.get('type')!r}")
    source_hash = str(info["hash"])
    if not re.fullmatch(r"[0-9a-fA-F]{64}", source_hash):
        raise FetchError("来源站元数据 hash 字段非法")

    file_resp = _get(f"{site}/textures/{source_hash}", user_agent, "纹理文件")
    if file_resp.status_code in (403, 404):
        raise FetchError("纹理文件不可下载")
    if file_resp.status_code != 200:
        raise FetchError(f"纹理文件返回 HTTP {file_resp.status_code}")
    png = file_resp.content
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise FetchError("下载内容不是 PNG 文件")
    if len(png) > MAX_DOWNLOAD_BYTES:
        raise FetchError("纹理文件超过体积上限（8MiB）")

    import math

    size_kb = int(info.get("size")) or math.ceil(len(png) / 1024)
    name = str(info.get("name") or "")[:50] or f"import-{tid}"
    return {
        "name": name,
        "type": str(info["type"]),
        "size_kb": max(1, int(size_kb)),
        "png": png,
    }
