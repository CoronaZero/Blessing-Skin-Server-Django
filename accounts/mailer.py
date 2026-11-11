"""动态邮件后端：SMTP 参数从 Option 表读取（管理后台配置）。

对照原版「后台 → 电子邮件设置」能力：运行时改参数立即生效，
无需重启进程。未配置 host 时回落到 settings 的默认后端（开发：控制台）。
"""

from django.conf import settings
from django.core.mail import get_connection

from options.models import Option


def get_option_smtp_connection():
    """按 Option 构建 SMTP 连接；未配置时返回默认后端连接。"""
    host = (Option.objects.get_value("mail_host") or "").strip()
    if not host:
        return get_connection(backend=settings.EMAIL_BACKEND)

    try:
        port = int(Option.objects.get_value("mail_port") or 465)
    except ValueError:
        port = 465
    encryption = (Option.objects.get_value("mail_encryption") or "ssl").strip().lower()
    return get_connection(
        backend="django.core.mail.backends.smtp.EmailBackend",
        host=host,
        port=port,
        username=(Option.objects.get_value("mail_username") or "").strip(),
        password=Option.objects.get_value("mail_password") or "",
        use_ssl=encryption == "ssl",
        use_tls=encryption in ("tls", "starttls"),
        fail_silently=True,
    )


def send_option_mail(subject, message, recipient_list, html_message=None):
    """用 option 配置的 SMTP 发送邮件（html_message 提供时为双通道）。"""
    from django.core.mail import send_mail

    configured = (Option.objects.get_value("mail_from_address") or "").strip()
    from_addr = configured or settings.DEFAULT_FROM_EMAIL
    return send_mail(
        subject=subject,
        message=message,
        from_email=from_addr,
        recipient_list=recipient_list,
        html_message=html_message,  # None 时纯文本；提供时邮件客户端友好渲染
        fail_silently=True,
        connection=get_option_smtp_connection(),
    )


def render_mail(template_name: str, context: dict) -> str:
    """渲染 HTML 邮件模板（templates/mail/ 下）。"""
    from django.template.loader import render_to_string

    return render_to_string(template_name, context)


def send_templated_mail(template_name: str, subject_prefix: str, recipient_list, context: dict):
    """发送模板化 HTML 邮件（正文纯文本版由调用方同时提供回落）。

    template_name 形如 "mail/reset_password.html"；
    context 需含 site_name / link 等模板变量。
    """
    site_name = context.get("site_name") or (
        Option.objects.get_value("site_name") or "Blessing Skin"
    )
    context = {**context, "site_name": site_name}
    html = render_mail(template_name, context)

    # 纯文本回落：标题 + 链接（保证任何客户端可读）
    link = context.get("link") or ""
    plain = "\n".join(filter(None, [subject_prefix, "", link]))

    return send_option_mail(
        subject=subject_prefix,
        message=plain,
        recipient_list=recipient_list,
        html_message=html,
    )
