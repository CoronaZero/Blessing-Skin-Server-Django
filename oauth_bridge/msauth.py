"""Microsoft OAuth 授权与 Minecraft 正版验证链路。

登录/注册流程：Azure AD v2 authorization code + id_token（openid email profile
scope），id_token 直接来自 HTTPS token 端点响应，按 RFC 允许免验签解码取
sub/email/name（sub 为稳定标识，绝不以 email 匹配账号）。

正版绑定流程（对照 mojang-verification 插件 MicrosoftProvider.php /
wiki.vg Microsoft Authentication Scheme，错误语义对齐 HMCL）：
    Microsoft OAuth(XboxLive.signin) → XBL → XSTS → login_with_xbox → profile

错误处理约定（排障硬要求）：
- 每步失败都把上游 HTTP 状态码与 error/XErr 摘要写进日志（logger 名
  oauth_bridge.msauth，gunicorn 日志可直接检索）；
- 用户可见消息附上游摘要，不吞响应体——"未购买游戏"仅由 profile 404
  判定，login_with_xbox 失败不再冒用该文案。
"""

import logging
from urllib.parse import urlencode

import jwt

from . import httpclient

logger = logging.getLogger(__name__)

TENANT = "consumers"  # 个人 Microsoft 账号（玩家场景），不做配置项
AUTHORIZE_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
SCOPE_LOGIN = "openid email profile"
SCOPE_XBOX = "XboxLive.signin"

XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"

# XSTS 错误码 → 用户可读原因
# （对照 HMCL MicrosoftService.XboxAuthorizationException 常量与注释：
#   2148916227=BANNED、2148916233=MISSING_XBOX_ACCOUNT、
#   2148916235=COUNTRY_UNAVAILABLE、2148916238=ADD_FAMILY）
_XSTS_ERRORS = {
    2148916227: "该 Xbox 账号已被封禁，无法完成正版验证",
    2148916233: "该 Microsoft 账号没有 Xbox 档案，请先在 xbox.com 创建",
    2148916235: "该 Microsoft 账号所在地区不支持 Xbox Live 服务",
    2148916236: "该地区无法访问 Xbox Live 服务（地区限制）",
    2148916237: "该地区无法访问 Xbox Live 服务（地区限制）",
    2148916238: "该账号为未成年帐号且未加入家庭组，无法完成 Xbox 认证",
}


class MicrosoftAuthError(Exception):
    """链路失败：message 面向用户中文提示，kind 供日志归因。"""

    def __init__(self, message: str, kind: str = "generic"):
        super().__init__(message)
        self.message = message
        self.kind = kind


def _fail(message: str, kind: str) -> MicrosoftAuthError:
    """统一失败出口：先写日志（含 kind 与消息）再抛出。"""
    logger.warning("正版链路失败 kind=%s message=%s", kind, message)
    return MicrosoftAuthError(message, kind)


def _upstream_error(resp) -> str:
    """上游错误摘要：HTTP 状态码 + error/XErr 字段（不吞响应体）。"""
    status = getattr(resp, "status_code", 0)
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 - 响应体非 JSON（WAF 页等）
        data = None
    if isinstance(data, dict):
        for key in ("error", "XErr", "Message", "errorMessage"):
            if data.get(key) not in (None, ""):
                return f"上游 HTTP {status}，{key}={str(data[key])[:80]}"
    text = (getattr(resp, "text", "") or "").strip()[:60]
    return f"上游 HTTP {status}" + (f"，{text}" if text else "")


def _response_json(resp):
    """安全解析 JSON；失败返回空 dict（错误信息交给 _upstream_error）。"""
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def authorize_url(client_id: str, redirect_uri: str, scope: str, state: str) -> str:
    """构造授权跳转地址（response_mode 固定 query，回调 GET 取 code 简单可靠）。"""
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": client_id,
        "response_type": "code",
        "response_mode": "query",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "prompt": "select_account",
    })


def exchange_code(client_id: str, client_secret: str, code: str,
                  redirect_uri: str, scope: str) -> dict:
    """授权码换 token（网络异常由 httpclient 重试，业务失败统一抛错）。"""
    try:
        resp = httpclient.outbound_post(TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "scope": scope,
        })
    except Exception:  # noqa: BLE001 - 网络层任何异常都不外泄细节
        raise _fail("无法连接 Microsoft 授权服务器", "token_network") from None
    if resp.status_code != 200:
        raise _fail(f"Microsoft 授权失败（{_upstream_error(resp)}）", "token_status")
    payload = _response_json(resp)
    if not payload.get("access_token"):
        raise _fail("Microsoft 授权响应缺少访问令牌", "token_payload")
    return payload


def parse_id_token(id_token: str) -> dict:
    """解 id_token 取身份（免验签：token 直连来自 HTTPS token 端点）。"""
    try:
        claims = jwt.decode(id_token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        raise _fail("Microsoft 身份令牌无效", "id_token") from None
    if not claims.get("sub"):
        raise _fail("Microsoft 身份令牌缺少 sub", "id_token") from None
    return {
        "sub": claims["sub"],
        "email": claims.get("email") or "",
        "name": claims.get("name") or "",
    }


def fetch_login_identity(client_id: str, client_secret: str, code: str,
                         redirect_uri: str) -> dict:
    """登录/注册流程：换 token 并解出 {sub, email, name}。"""
    token = exchange_code(client_id, client_secret, code, redirect_uri, SCOPE_LOGIN)
    id_token = token.get("id_token")
    if not id_token:
        raise _fail("Microsoft 授权响应缺少 id_token", "id_token_missing") from None
    return parse_id_token(id_token)


def _xsts_error_message(payload: dict) -> str:
    xerr = payload.get("XErr")
    if isinstance(xerr, int) and xerr in _XSTS_ERRORS:
        return _XSTS_ERRORS[xerr]
    return f"Xbox 认证失败（错误码 {xerr}）"


def xbox_authenticate(msa_access_token: str) -> tuple[dict, str]:
    """正版绑定流程：Microsoft access token → (Minecraft profile, MC token)。

    五步链路任何一步失败都抛 MicrosoftAuthError（中文原因 + 上游摘要）；
    成功返回 profile（含 id/name/properties）与 Minecraft access token。
    """
    # 1. Xbox Live user authenticate
    try:
        resp = httpclient.outbound_post(XBL_AUTH_URL, json={
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={msa_access_token}",
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
        })
    except Exception:  # noqa: BLE001
        raise _fail("无法连接 Xbox Live 认证服务", "xbl_network") from None
    if resp.status_code != 200:
        raise _fail(f"Xbox Live 认证失败（{_upstream_error(resp)}）", "xbl_status")
    data = _response_json(resp)
    xbl_token = data.get("Token") or ""
    uhs = (data.get("DisplayClaims") or {}).get("xui") or [{}]
    uhs = (uhs[0] or {}).get("uhs", "") if uhs else ""
    if not xbl_token or not uhs:
        raise _fail(f"Xbox Live 认证响应不完整（{_upstream_error(resp)}）",
                    "xbl_payload") from None

    # 2. XSTS authorize（XErr 存在即为失败，不看 HTTP status——PHP/HMCL 同语义）
    try:
        resp = httpclient.outbound_post(XSTS_AUTH_URL, json={
            "Properties": {
                "SandboxId": "RETAIL",
                "UserTokens": [xbl_token],
            },
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        })
    except Exception:  # noqa: BLE001
        raise _fail("无法连接 Xbox XSTS 认证服务", "xsts_network") from None
    xsts_payload = _response_json(resp)
    if xsts_payload.get("XErr") is not None:
        raise _fail(_xsts_error_message(xsts_payload), "xsts_denied")
    xsts_token = xsts_payload.get("Token") or ""
    if resp.status_code != 200 or not xsts_token:
        raise _fail(f"XSTS 认证失败（{_upstream_error(resp)}）", "xsts_status")

    # 3. Minecraft login_with_xbox（此步失败常见原因：网络风控/Azure 应用
    #    未获 Minecraft API 许可——绝不冒用"未购买"文案）
    try:
        resp = httpclient.outbound_post(MC_LOGIN_URL, json={
            "identityToken": f"XBL3.0 x={uhs};{xsts_token}",
        })
    except Exception:  # noqa: BLE001
        raise _fail("无法连接 Minecraft 认证服务", "mc_login_network") from None
    mc_payload = _response_json(resp) if resp.status_code == 200 else {}
    if not mc_payload.get("access_token"):
        raise _fail(
            f"Minecraft 账号认证失败（{_upstream_error(resp)}）",
            "mc_login",
        )

    mc_token = mc_payload["access_token"]

    # 4. 拉取 Minecraft profile
    try:
        resp = httpclient.outbound_get(
            MC_PROFILE_URL, headers={"Authorization": f"Bearer {mc_token}"}
        )
    except Exception:  # noqa: BLE001
        raise _fail("无法连接 Minecraft 配置服务", "profile_network") from None
    if resp.status_code == 404:
        # 未购买的真实判定位置（404 / NOT_FOUND），与 login_with_xbox 失败区分
        raise _fail(
            "该 Microsoft 账号未购买 Minecraft: Java Edition，无法绑定正版角色",
            "profile_not_purchased",
        ) from None
    profile = _response_json(resp)
    if profile.get("error"):
        err = str(profile["error"])
        if err == "CONSTRAINT_VIOLATION":
            raise _fail("该账号所在地区无法访问 Minecraft 服务", "profile_constraint")
        raise _fail(f"获取 Minecraft 角色信息失败（error={err}）", "profile_error")
    if resp.status_code != 200 or not profile.get("id") or not profile.get("name"):
        raise _fail(f"获取 Minecraft 角色信息失败（{_upstream_error(resp)}）",
                    "profile_status") from None
    return profile, mc_token
