"""面板 SMTP 测试邮件端点。"""

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from config.jsonapi import FAILURE, SUCCESS, json_response
from options.models import Option

from .guards import super_admin_required


@super_admin_required
@require_POST
def mail_test(request):
    """用当前 Options 的 SMTP 配置发送一封测试邮件。"""
    recipient = (request.POST.get("email") or "").strip()
    if "@" not in recipient or "." not in recipient.split("@", 1)[1]:
        return json_response("请输入有效的收件邮箱", FAILURE)

    from accounts.mailer import render_mail, send_option_mail

    site_name = Option.objects.get_value("site_name") or "Blessing Skin"
    html = render_mail("mail/reset_password.html", {
        "site_name": site_name,
        "link": "https://example.com/this-is-a-test-email",
    })
    plain = f"这是一封来自 {site_name} 的 SMTP 配置测试邮件。"

    try:
        sent = send_option_mail(
            subject=f"【{site_name}】SMTP 配置测试",
            message=plain,
            recipient_list=[recipient],
            html_message=html,
        )
    except Exception as exc:  # noqa: BLE001 - SMTP 异常统一呈现
        return json_response(f"发送异常：{exc}", FAILURE)

    if sent == 0:
        host = (Option.objects.get_value("mail_host") or "").strip()
        if not host:
            return json_response(
                "mail_host 未配置 → 邮件进了控制台后端而非真实发送；"
                "填写 SMTP 服务器后重试",
                FAILURE,
            )
        return json_response(
            "发送失败（SMTP 拒绝）：检查端口/加密方式/用户名/授权码，"
            "并确认服务商已开 SMTP",
            FAILURE,
        )
    return JsonResponse({
        "code": SUCCESS,
        "message": f"测试邮件已发送 → {recipient}，未收到请查垃圾箱",
    })
