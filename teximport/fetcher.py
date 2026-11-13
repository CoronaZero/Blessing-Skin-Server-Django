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

MAX_REDIRECTS = 3


def _get(url: str, user_agent: str, tag: str) -> http_client.Response:
    """带重试与逐跳 SSRF 校验的 GET。

    allow_redirects=False + 手动跟随：每一跳都重新过 assert_safe_host，
    防止公网站点 302 跳板转向内网/云元数据地址（若交给 requests 自动
    跟随，只有首跳会被校验）。标签用于错误信息区分。
    """
    last_error = None
    for _attempt in range(3):
        resp = None
        try:
            current = url
            for _hop in range(MAX_REDIRECTS + 1):
                # 逐跳校验目标主机（重定向地址不可信）
                parts = urlsplit(current)
                assert_safe_host(parts.netloc)
                resp = http_client.get(
                    current,
                    timeout=REQUEST_TIMEOUT,
                    headers={"User-Agent": user_agent},
                    allow_redirects=False,
                    stream=True,
                )
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    resp.close()
                    if not location:
                        raise FetchError(f"{tag}重定向缺少 Location")
                    current = _resolve_url(current, location)
                    continue
                if resp.status_code in RETRYABLE_STATUS:
                    last_error = f"HTTP {resp.status_code}"
                    resp.close()
                    break  # 跳出跳循环，进入下一次外层重试
                return resp
            raise FetchError(f"{tag}重定向次数超过 {MAX_REDIRECTS}")
        except http_client.RequestException as exc:
            last_error = exc
    raise FetchError(f"{tag}请求失败（已重试）：{last_error}")


def _resolve_url(base: str, location: str) -> str:
    """相对/绝对 Location 解析为绝对 URL。"""
    from urllib.parse import urljoin

    return urljoin(base, location)


def _read_capped(resp: http_client.Response) -> bytes:
    """流式读取响应体，超过 MAX_DOWNLOAD_BYTES 立即断开。

    必须流式：resp.content 会先把完整响应载入内存再谈上限——
    恶意超大/chunked 响应会直接 OOM 进程且卡死唯一队列线程。
    """
    try:
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
            raise FetchError("纹理文件超过体积上限（8MiB）")
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise FetchError("纹理文件超过体积上限（8MiB）")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        resp.close()


def fetch_texture(site: str, tid: int, user_agent: str) -> dict:
    """拉取单件纹理：元数据 + PNG 内容。

    返回 {"name", "type", "hash", "size_kb", "png": bytes}。
    """
    info_resp = _get(f"{site}/skinlib/info/{tid}", user_agent, "元数据")
    try:
        if info_resp.status_code in (403, 404):
            raise FetchError("来源站返回纹理不存在或非公开")
        if info_resp.status_code != 200:
            raise FetchError(f"元数据接口返回 HTTP {info_resp.status_code}")
        try:
            info = json.loads(_read_capped(info_resp))
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
        png = _read_capped(file_resp)
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise FetchError("下载内容不是 PNG 文件")

        import math

        size_kb = int(info.get("size")) or math.ceil(len(png) / 1024)
        name = str(info.get("name") or "")[:50] or f"import-{tid}"
        return {
            "name": name,
            "type": str(info["type"]),
            "size_kb": max(1, int(size_kb)),
            "png": png,
        }
    finally:
        info_resp.close()
