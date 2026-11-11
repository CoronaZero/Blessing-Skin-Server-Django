"""验证码连通性测试端点（管理面板）。"""

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from config.jsonapi import FAILURE, SUCCESS, json_response
from options.models import Option

from .guards import super_admin_required


@super_admin_required
@require_POST
def captcha_test(request):
    """对当前配置的验证码服务发起连通性探测。

    用无效 token 调 siteverify：能区分「网络通、密钥有效」（返回
    invalid-input-response）、「密钥错误」（invalid-input-secret）、
    「网络不通/超时」三种状态——足够判断配置是否可用。
    """
    provider = (request.POST.get("provider") or
                Option.objects.get_value("captcha_provider") or "").strip()
    if provider not in ("turnstile", "recaptcha"):
        return json_response("请先选择 turnstile 或 recaptcha 提供者", FAILURE)

    import json
    import urllib.request

    if provider == "turnstile":
        secret = Option.objects.get_value("turnstile_secret") or ""
        url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
        sitekey = Option.objects.get_value("turnstile_sitekey") or ""
    else:
        secret = Option.objects.get_value("recaptcha_secret") or ""
        url = "https://www.recaptcha.net/recaptcha/api/siteverify"
        sitekey = Option.objects.get_value("recaptcha_sitekey") or ""

    findings = []
    if not sitekey:
        findings.append("Sitekey 未配置")
    if not secret:
        findings.append("Secret 未配置")
    if findings:
        return json_response("；".join(findings), FAILURE)

    data = "&".join(
        f"{k}={urllib.request.quote(str(v))}"
        for k, v in {"secret": secret, "response": "probe-invalid-token"}.items()
    ).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - 白名单 URL
            body = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 - 网络异常统一报告
        return json_response(f"无法连接验证服务：{exc}", FAILURE)

    errors = body.get("error-codes") or []
    if "invalid-input-secret" in errors:
        return json_response("网络连通，但 Secret 无效——请检查密钥", FAILURE)
    if "invalid-input-response" in errors or "timeout-or-duplicate" in errors:
        # 探测用假 token 必然返回此错误——说明 secret 验证通过，服务可用
        return JsonResponse({
            "code": SUCCESS,
            "message": "连通性正常：服务可达且 Secret 有效"
                        "（验证须在前台真实 widget 完成后进行）",
        })
    return json_response(f"未知响应：{errors or body}", FAILURE)
