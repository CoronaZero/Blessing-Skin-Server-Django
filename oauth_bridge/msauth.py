"""Microsoft OAuth 授权与 Minecraft 正版验证链路。

登录/注册流程：Azure AD v2 authorization code + id_token（openid email profile
scope），id_token 直接来自 HTTPS token 端点响应，按 RFC 允许免验签解码取
sub/email/name（sub 为稳定标识，绝不以 email 匹配账号）。

正版绑定流程（对照 mojang-verification 插件 MicrosoftProvider.php /
wiki.vg Microsoft Authentication Scheme）：
    Microsoft OAuth(XboxLive.signin) → XBL → XSTS → login_with_xbox → profile
"""

from urllib.parse import urlencode

import jwt

from . import httpclient

TENANT = "consumers"  # 个人 Microsoft 账号（玩家场景），不做配置项
AUTHORIZE_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
SCOPE_LOGIN = "openid email profile"
SCOPE_XBOX = "XboxLive.signin"

XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"

# XSTS 错误码 → 用户可读原因（对照 wiki.vg 与插件实践）
_XSTS_ERRORS = {
    2148916233: "该 Microsoft 账号受家长限制或未成年，无法登录 Xbox Live",
    2148916235: "该 Microsoft 账号所在地区不支持 Xbox Live 服务",
    2148916236: "该地区无法访问 Xbox Live 服务（地区限制）",
    2148916237: "该地区无法访问 Xbox Live 服务（地区限制）",
    2148916238: "该 Microsoft 账号没有 Xbox 档案，请先在 xbox.com 创建",
}


class MicrosoftAuthError(Exception):
    """链路失败：message 面向用户中文提示，kind 供日志归因。"""

    def __init__(self, message: str, kind: str = "generic"):
        super().__init__(message)
        self.message = message
        self.kind = kind


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
    """授权码换 token（失败统一抛 MicrosoftAuthError，调用方不区分网络/业务错）。"""
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
        raise MicrosoftAuthError("无法连接 Microsoft 授权服务器", "token_network") from None
    if resp.status_code != 200:
        raise MicrosoftAuthError("Microsoft 授权失败（code 无效或已过期）", "token_status")
    payload = resp.json()
    if not payload.get("access_token"):
        raise MicrosoftAuthError("Microsoft 授权响应缺少访问令牌", "token_payload")
    return payload


def parse_id_token(id_token: str) -> dict:
    """解 id_token 取身份（免验签：token 直连来自 HTTPS token 端点）。"""
    try:
        claims = jwt.decode(id_token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        raise MicrosoftAuthError("Microsoft 身份令牌无效", "id_token") from None
    if not claims.get("sub"):
        raise MicrosoftAuthError("Microsoft 身份令牌缺少 sub", "id_token")
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
        raise MicrosoftAuthError("Microsoft 授权响应缺少 id_token", "id_token_missing")
    return parse_id_token(id_token)


def _xsts_error_message(payload: dict) -> str:
    xerr = payload.get("XErr")
    if isinstance(xerr, int) and xerr in _XSTS_ERRORS:
        return _XSTS_ERRORS[xerr]
    return f"Xbox 认证失败（错误码 {xerr}）"


def xbox_authenticate(msa_access_token: str) -> tuple[dict, str]:
    """正版绑定流程：Microsoft access token → (Minecraft profile, MC token)。

    五步链路任何一步失败都抛 MicrosoftAuthError（中文原因）；
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
        raise MicrosoftAuthError("无法连接 Xbox Live 认证服务", "xbl_network") from None
    if resp.status_code != 200:
        raise MicrosoftAuthError("Xbox Live 认证失败", "xbl_status")
    data = resp.json()
    xbl_token = data.get("Token") or ""
    uhs = (data.get("DisplayClaims", {}).get("xui") or [{}])[0].get("uhs", "")
    if not xbl_token or not uhs:
        raise MicrosoftAuthError("Xbox Live 认证响应不完整", "xbl_payload")

    # 2. XSTS authorize
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
        raise MicrosoftAuthError("无法连接 Xbox XSTS 认证服务", "xsts_network") from None
    if resp.status_code in (401, 403):
        raise MicrosoftAuthError(_xsts_error_message(resp.json()), "xsts_denied")
    if resp.status_code != 200:
        raise MicrosoftAuthError("XSTS 认证失败", "xsts_status")
    xsts_token = resp.json().get("Token") or ""
    if not xsts_token:
        raise MicrosoftAuthError("XSTS 认证响应缺少令牌", "xsts_payload")

    # 3. Minecraft login_with_xbox
    try:
        resp = httpclient.outbound_post(MC_LOGIN_URL, json={
            "identityToken": f"XBL3.0 x={uhs};{xsts_token}",
        })
    except Exception:  # noqa: BLE001
        raise MicrosoftAuthError("无法连接 Minecraft 认证服务", "mc_login_network") from None
    if resp.status_code != 200 or not resp.json().get("access_token"):
        raise MicrosoftAuthError("Minecraft 账号认证失败（可能未购买游戏）", "mc_login")

    mc_token = resp.json()["access_token"]

    # 4. 拉取 Minecraft profile
    try:
        resp = httpclient.outbound_get(
            MC_PROFILE_URL, headers={"Authorization": f"Bearer {mc_token}"}
        )
    except Exception:  # noqa: BLE001
        raise MicrosoftAuthError("无法连接 Minecraft 配置服务", "profile_network") from None
    if resp.status_code == 404:
        raise MicrosoftAuthError(
            "该 Microsoft 账号未购买 Minecraft: Java Edition，无法绑定正版角色",
            "profile_not_purchased",
        )
    if resp.status_code != 200:
        raise MicrosoftAuthError("获取 Minecraft 角色信息失败", "profile_status")
    profile = resp.json()
    if not profile.get("id") or not profile.get("name"):
        raise MicrosoftAuthError("Minecraft 角色信息响应不完整", "profile_payload")
    return profile, mc_token
