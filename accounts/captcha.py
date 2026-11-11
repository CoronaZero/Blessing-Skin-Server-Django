"""验证码提供者抽象：none（默认）/ Turnstile / reCAPTCHA，option 驱动。"""

import json
import urllib.request

from options.models import Option


def get_provider() -> str:
    """当前启用的验证码提供者（option captcha_provider）。"""
    return (Option.objects.get_value("captcha_provider") or "").strip().lower()


def get_captcha_requirements() -> dict:
    """登录/注册页需要的验证码参数（None 表示不启用）。"""
    provider = get_provider()
    if not provider:
        return {"provider": None}
    if provider == "turnstile":
        sitekey = Option.objects.get_value("turnstile_sitekey") or ""
        return {"provider": "turnstile", "sitekey": sitekey}
    if provider == "recaptcha":
        sitekey = Option.objects.get_value("recaptcha_sitekey") or ""
        return {"provider": "recaptcha", "sitekey": sitekey}
    return {"provider": None}


def verify_captcha(token, ip=None) -> bool:
    """按当前提供者校验 token；未启用直接放行。"""
    provider = get_provider()
    if not provider:
        return True
    if not token:
        return False
    try:
        if provider == "turnstile":
            return bool(_http_verify_turnstile(token, ip))
        if provider == "recaptcha":
            return bool(_http_verify_recaptcha(token))
    except Exception:  # noqa: BLE001 - 网络异常按校验失败处理
        return False
    return False


def _post_form(url: str, form: dict) -> dict:
    data = "&".join(
        f"{k}={urllib.request.quote(str(v))}" for k, v in form.items() if v is not None
    ).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - 固定白名单 URL
        body = json.loads(resp.read().decode())
    return body


def _http_verify_turnstile(token: str, ip=None) -> bool:
    """Cloudflare Turnstile siteverify。"""
    secret = Option.objects.get_value("turnstile_secret") or ""
    body = _post_form(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        {"secret": secret, "response": token, "remoteip": ip},
    )
    return bool(body.get("success"))


def _http_verify_recaptcha(token: str) -> bool:
    """Google reCAPTCHA v2（兼容原版配置迁移）。"""
    import urllib.parse

    secret = Option.objects.get_value("recaptcha_secret") or ""
    data = urllib.parse.urlencode(
        {"secret": secret, "response": token}
    ).encode()
    req = urllib.request.Request(
        "https://www.recaptcha.net/recaptcha/api/siteverify", data=data
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        body = json.loads(resp.read().decode())
    return bool(body.get("success"))
