"""accounts 视图：登录/注册/找回/重置/邮箱验证。

对照 PHP 原版 AuthController 全部登录语义，含三项安全修复：
① 账号枚举防护（option login_enumeration_protection，默认关保持原版行为）
② 找回密码响应与是否已发送一致（防枚举）
③ 重置链接一次性（PasswordReset 令牌模型）
"""

import hashlib

from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.core.cache import cache
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from config.jsonapi import FAILURE, SPECIAL, SUCCESS, json_response
from options.models import Option

from .captcha import get_captcha_requirements, verify_captcha
from .mailer import send_option_mail
from .models import PasswordReset, User


def _client_ip(request) -> str:
    """客户端 IP（X-Forwarded-For 首跳，无则 REMOTE_ADDR）。"""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


# ---- 登录 ----


@require_GET
def login_page(request):
    """登录页占位（Tailwind 前台 Phase 5 替换）。"""
    return render(
        request,
        "accounts/login.html",
        {"captcha": get_captcha_requirements()},
    )


@require_POST
def login_submit(request):
    identification = request.POST.get("identification", "").strip()
    password = request.POST.get("password", "")

    if not identification or not password:
        return json_response("请填写邮箱/角色名与密码")

    ip = _client_ip(request)
    fails_key = "login_fails:" + hashlib.sha256(ip.encode()).hexdigest()
    fails = cache.get(fails_key, 0)

    # 连续失败 > 3 时强制验证码（对照原版 login_fails 逻辑）
    if fails > 3:
        if not verify_captcha(request.POST.get("captcha"), ip=ip):
            return json_response("请完成人机验证")

    enum_flag = Option.objects.get_value("login_enumeration_protection") or ""
    enumeration_protected = enum_flag.lower() in ("1", "on", "true")

    user = authenticate(request, username=identification, password=password)
    if user is None:
        # 统一文案：开启枚举防护后不区分“用户不存在/密码错误”
        message = "账号或密码错误"
        code = FAILURE
        if not enumeration_protected:
            exists = _user_exists(identification)
            message = "该账号不存在" if not exists else "密码错误"
            code = SPECIAL if not exists else FAILURE
        cache.set(fails_key, fails + 1, 3600)
        return json_response(message, code)

    if user.permission == User.Permission.BANNED:
        return json_response("账号已被封禁")

    auth_login(request, user)
    cache.delete(fails_key)
    redirect_to = request.session.pop("last_requested_path", "/user")
    return json_response("登录成功", SUCCESS, {"redirectTo": redirect_to})


def _user_exists(identification: str) -> bool:
    from players.models import Player

    if "@" in identification:
        return User.objects.filter(email=identification).exists()
    return Player.objects.filter(name=identification).exists()


# ---- 注册 ----


@require_GET
def register_page(request):
    return render(
        request,
        "accounts/register.html",
        {"captcha": get_captcha_requirements()},
    )


@require_POST
def register_submit(request):
    # disable-registration 内嵌能力
    if (Option.objects.get_value("disable_registration") or "0") in ("1", "true"):
        return json_response("本站已关闭注册")

    if not verify_captcha(request.POST.get("captcha"), ip=_client_ip(request)):
        return json_response("人机验证未通过")

    email = request.POST.get("email", "").strip()
    password = request.POST.get("password", "")
    nickname = (request.POST.get("nickname") or "").strip()

    if not email or "@" not in email:
        return json_response("请填写有效邮箱")
    if len(password) < 8 or len(password) > 32:
        return json_response("密码长度需为 8-32 位")
    if User.objects.filter(email=email).exists():
        return json_response("该邮箱已被注册")
    if not nickname:
        nickname = email.split("@")[0]

    # 邀请码内嵌能力
    invitation_required = (Option.objects.get_value("invitation_required") or "0") in ("1", "true")
    invitation = None
    if invitation_required:
        from invitation.models import InvitationCode

        code_value = (request.POST.get("invitation_code") or "").strip()
        invitation = InvitationCode.objects.filter(
            code=code_value, used_by__isnull=True
        ).first()
        if invitation is None:
            return json_response("邀请码无效或已被使用")

    # regs_per_ip：同 IP 注册数上限（对照原版）
    ip = _client_ip(request)
    try:
        regs_limit = int(Option.objects.get_value("regs_per_ip") or 3)
    except ValueError:
        regs_limit = 3
    if User.objects.filter(ip=ip).count() >= regs_limit:
        return json_response(f"该 IP 注册数已达上限（{regs_limit}）")

    user = User.objects.create_user(
        email=email, nickname=nickname, password=password, ip=ip
    )

    if invitation is not None:
        from django.utils import timezone

        invitation.used_by = user
        invitation.used_at = timezone.now()
        invitation.save(update_fields=["used_by", "used_at"])

    if (Option.objects.get_value("require_verification") or "0") in ("1", "true"):
        _send_verification_mail(user)

    auth_login(request, user)
    return json_response("注册成功", SUCCESS, {"redirectTo": "/user"})


# ---- 登出 ----


@require_POST
def logout_submit(request):
    if not request.user.is_authenticated:
        return json_response("尚未登录", FAILURE)
    auth_logout(request)
    return json_response("已退出登录", SUCCESS)


# ---- 找回密码 / 重置 ----


@require_GET
def forgot_page(request):
    return render(request, "accounts/forgot.html")


@require_POST
def forgot_submit(request):
    email = request.POST.get("email", "").strip()
    if not email or "@" not in email:
        return json_response("请填写有效邮箱")

    if not verify_captcha(request.POST.get("captcha"), ip=_client_ip(request)):
        return json_response("人机验证未通过")

    # IP 限速 180s（对照原版 last_mail）
    ip = _client_ip(request)
    mail_key = "last_mail:" + hashlib.sha256(ip.encode()).hexdigest()
    if cache.get(mail_key) is not None:
        return json_response("邮件发送过于频繁，请稍后再试", SPECIAL)

    user = User.objects.filter(email=email).first()
    if user is not None:
        reset = PasswordReset.issue(user)
        site_name = Option.objects.get_value("site_name") or "Blessing Skin"
        link = f"{_base_url(request)}{reverse('accounts:reset_page')}"
        link = f"{link}?uid={user.uid}&token={reset.token}"
        send_option_mail(
            subject=f"【{site_name}】重置你的密码",
            message=f"你（或他人）在 {site_name} 申请了密码重置。\n\n"
            f"点击以下链接重置密码（1 小时内有效，仅可使用一次）：\n{link}\n\n"
            f"如果这不是你的操作，请忽略本邮件。",
            recipient_list=[email],
        )
        cache.set(mail_key, 1, 180)  # 180s 冷却（对照原版 rate limit）

    # 防枚举：无论邮箱是否存在，响应一致
    return json_response("如果该邮箱已注册，重置邮件已发送", SUCCESS)


@require_GET
def reset_page(request):
    return render(request, "accounts/reset.html")


@require_POST
def reset_submit(request):
    # token/uid 允许来自 URL query（邮件链接直 POST）或表单体
    uid = request.POST.get("uid") or request.GET.get("uid", "")
    token = request.POST.get("token") or request.GET.get("token", "")
    password = request.POST.get("password", "")
    confirm = request.POST.get("password_confirmation", "")

    try:
        user = User.objects.get(uid=int(uid)) if uid else None
    except (ValueError, User.DoesNotExist):
        user = None
    if user is None:
        return json_response("重置链接无效")

    if len(password) < 8 or len(password) > 32:
        return json_response("密码长度需为 8-32 位")
    if confirm and password != confirm:
        return json_response("两次输入的密码不一致")
    if token.startswith("Bearer "):
        token = token[7:]

    if not PasswordReset.consume(user, token):
        return json_response("重置链接无效或已失效")

    user.set_password(password)
    user.save(update_fields=["password"])
    # 密码重置后吊销当前会话（安全修复：改密后全端下线）
    request.session.flush()
    return json_response("密码已重置", SUCCESS)


# ---- 邮箱验证 ----


def _send_verification_mail(user):
    from .verification import make_verification_token

    token = make_verification_token(user)
    site_name = Option.objects.get_value("site_name") or "Blessing Skin"
    link = f"{_default_base_url()}/{reverse('accounts:verify', args=[token]).lstrip('/')}"
    send_option_mail(
        subject=f"【{site_name}】验证你的邮箱",
        message=f"欢迎注册 {site_name}！\n\n点击以下链接完成邮箱验证（1 小时内有效）：\n{link}",
        recipient_list=[user.email],
    )


def verify_email(request, token):
    from .verification import resolve_verification_token

    user = resolve_verification_token(token)
    if user is None:
        return render(request, "accounts/verify.html", {"ok": False})
    user.verified = True
    user.save(update_fields=["verified"])
    return render(request, "accounts/verify.html", {"ok": True})


def _base_url(request) -> str:
    scheme = "https" if request.is_secure() else "http"
    return f"{scheme}://{request.get_host()}"


def _default_base_url() -> str:
    from django.contrib.sites.models import Site

    domain = Site.objects.get_current().domain
    return f"http://{domain}"
