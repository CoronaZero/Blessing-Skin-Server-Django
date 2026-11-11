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


def send_option_mail(subject, message, recipient_list):
    """用 option 配置的 SMTP 发送纯文本邮件。"""
    from django.core.mail import send_mail

    from_addr = (Option.objects.get_value("mail_from_address") or "").strip() or settings.DEFAULT_FROM_EMAIL
    return send_mail(
        subject=subject,
        message=message,
        from_email=from_addr,
        recipient_list=recipient_list,
        fail_silently=True,
        connection=get_option_smtp_connection(),
    )
