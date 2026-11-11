"""发送测试邮件：验证 SMTP 配置是否可用。

用法：
    python manage.py send_test_email you@example.com

配置来源（Option 表，管理后台 Options 中编辑）：
    mail_host / mail_port / mail_username / mail_password /
    mail_encryption / mail_from_address
未配置 mail_host 时走默认后端（开发=控制台打印，并提示这不是真实发送）。
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "发送测试邮件以验证 SMTP 配置（对照原版后台的测试邮件功能）"

    def add_arguments(self, parser):
        parser.add_argument("recipient", help="接收测试邮件的邮箱地址")

    def handle(self, *args, **options):
        recipient = (options["recipient"] or "").strip()
        if "@" not in recipient:
            raise CommandError("邮箱地址无效")

        from accounts.mailer import get_option_smtp_connection, render_mail
        from options.models import Option

        host = (Option.objects.get_value("mail_host") or "").strip()
        from_configured = (Option.objects.get_value("mail_from_address") or "").strip()

        self.stdout.write("=== SMTP 配置 ===")
        if not host:
            self.stdout.write(self.style.WARNING(
                "mail_host 未配置 → 走默认后端（开发=控制台），不是真实发送"
            ))
        else:
            port = Option.objects.get_value("mail_port") or "465"
            encryption = Option.objects.get_value("mail_encryption") or "ssl"
            user = (Option.objects.get_value("mail_username") or "") or "(空)"
            from_addr = from_configured or "(默认)"
            self.stdout.write(f"  host:       {host}:{port} ({encryption})")
            self.stdout.write(f"  username:   {user}")
            self.stdout.write(f"  from:       {from_addr}")

        from django.conf import settings

        html = render_mail("mail/reset_password.html", {
            "site_name": Option.objects.get_value("site_name") or "Blessing Skin",
            "link": "https://example.com/this-is-a-test-email",
        })
        plain = "这是一封测试邮件（Blessing Skin Django 版 SMTP 连通性验证）"

        from django.core.mail import EmailMultiAlternatives

        email = EmailMultiAlternatives(
            subject="【测试】Blessing Skin 邮件配置验证",
            body=plain,
            from_email=from_configured or settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            connection=get_option_smtp_connection(),
        )
        email.attach_alternative(html, "text/html")

        sent = email.send()  # fail_silently 由连接自身决定
        if sent == 1:
            self.stdout.write(self.style.SUCCESS(f"测试邮件已发送 → {recipient}"))
            self.stdout.write("若收件箱未收到，请检查垃圾箱与 SMTP 授权码")
        else:
            raise CommandError(
                "发送失败：SMTP 拒绝了连接/认证。检查 mail_host/端口/加密方式/授权码"
            )
