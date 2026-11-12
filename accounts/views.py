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
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from config.jsonapi import FAILURE, SPECIAL, SUCCESS, json_response
from options.models import Option

from .captcha import get_captcha_requirements, verify_captcha
from .mailer import send_templated_mail
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
    """登录页（Tailwind 风格）；受保护页跳转来时回显安全校验后的 next。"""
    next_url = request.GET.get("next", "")
    if next_url and not _safe_next(next_url):
        next_url = ""  # 外站 next 不回显（防 open redirect 钓鱼）
    return render(
        request,
        "accounts/login.html",
        {
            "captcha": get_captcha_requirements(),
            "site_name": Option.objects.get_value("site_name"),
            "next_url": next_url,
        },
    )


def _safe_next(candidate: str) -> bool:
    """仅放行站内相对路径（绝对地址/协议相对地址一律拒绝）。"""
    from django.utils.http import url_has_allowed_host_and_scheme

    return url_has_allowed_host_and_scheme(candidate, allowed_hosts=None)


@require_POST
def login_submit(request):
    identification = request.POST.get("identification", "").strip()
    password = request.POST.get("password", "")

    if not identification or not password:
        return json_response("请填写邮箱/角色名与密码")

    ip = _client_ip(request)
    fails_key = "login_fails:" + hashlib.sha256(ip.encode()).hexdigest()

    # 验证码 provider 启用时无条件强制（verify_captcha 在关闭时返回 True，
    # 不影响无验证码站点；此前仅 fails>3 才校验，启用了 widget 却可绕过）
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
        cache.set(fails_key, cache.get(fails_key, 0) + 1, 3600)
        return json_response(message, code)

    if user.permission == User.Permission.BANNED:
        return json_response("账号已被封禁")

    auth_login(request, user)
    cache.delete(fails_key)
    # 回跳优先级：安全校验后的 next > 会话记录 > 默认用户中心
    next_target = request.POST.get("next") or request.session.pop(
        "last_requested_path", ""
    )
    redirect_to = next_target if (next_target and _safe_next(next_target)) else "/user"
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
        {
            "captcha": get_captcha_requirements(),
            "site_name": Option.objects.get_value("site_name"),
            "invitation_required": (
                Option.objects.get_value("invitation_required") or "0"
            )
            in ("1", "true"),
        },
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
    # 后端要求验证码（handleForgot），页面必须渲染 widget，否则死锁
    return render(
        request,
        "accounts/forgot.html",
        {
            "captcha": get_captcha_requirements(),
            "site_name": Option.objects.get_value("site_name"),
        },
    )


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
        send_templated_mail(
            template_name="mail/reset_password.html",
            subject_prefix=f"【{site_name}】重置你的密码",
            recipient_list=[email],
            context={"link": link},
        )
        cache.set(mail_key, 1, 180)  # 180s 冷却（对照原版 rate limit）

    # 防枚举：无论邮箱是否存在，响应一致
    return json_response("如果该邮箱已注册，重置邮件已发送", SUCCESS)


@require_GET
def reset_page(request):
    return render(
        request,
        "accounts/reset.html",
        {"uid": request.GET.get("uid", ""), "token": request.GET.get("token", "")},
    )


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
    from .verification import make_verification_link

    link = make_verification_link(user)
    site_name = Option.objects.get_value("site_name") or "Blessing Skin"
    send_templated_mail(
        template_name="mail/verify_email.html",
        subject_prefix=f"【{site_name}】验证你的邮箱",
        recipient_list=[user.email],
        context={"link": link},
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


def _bs_user_json(user):
    """用户概要 JSON（模板注入 BS_USER 全局，纯辅助函数非视图）。"""
    import json as _json

    return _json.dumps(
        {
            "nickname": user.nickname,
            "email": user.email,
            "score": user.score,
            "players": [
                {"pid": p.pid, "name": p.name, "uuid": p.uuid}
                for p in user.players.all()
            ],
        },
        ensure_ascii=False,
    )


@login_required
def user_dashboard(request):
    """用户中心仪表盘（分页改造后的主页：概览+签到+最近通知）。"""

    recent_notifications = (
        request.user.notifications.order_by("-created_at")[:5]
    )
    announcement = Option.objects.get_value("site_announcement") or ""

    # 签到状态（与 daily_sign 的 20h 冷却一致）
    from datetime import timedelta

    last_sign = request.user.last_sign_at
    can_sign = not (last_sign and timezone.now() - last_sign < timedelta(hours=20))
    next_sign = (last_sign or timezone.now()) + timedelta(hours=20)
    next_sign_time = next_sign.strftime("%m-%d %H:%M")
    return render(
        request,
        "accounts/dashboard.html",
        {
            "site_name": Option.objects.get_value("site_name"),
            "bs_user_json": _bs_user_json(request.user),
            "recent_notifications": recent_notifications,
            "announcement": announcement,
        },
    )


def profile_view(request):
    """资料页（分页改造后专指资料设置）/ POST 修改资料。"""
    if request.method == "POST":
        return profile_submit(request)
    return render(
        request,
        "accounts/profile.html",
        {
            "site_name": Option.objects.get_value("site_name"),
            "bs_user_json": _bs_user_json(request.user),
        },
    )


# ---- 用户中心：资料 / 通知 / 签到 ----


@login_required
@require_POST
def profile_submit(request):
    """修改资料（对照原版 handleProfile action 分派）。"""
    action = request.POST.get("action", "")
    user = request.user

    if action == "nickname":
        nickname = (request.POST.get("new_nickname") or "").strip()
        if not nickname or len(nickname) > 32:
            return json_response("昵称长度须为 1-32 个字符")
        user.nickname = nickname
        user.save(update_fields=["nickname"])
        return json_response("昵称已更新", SUCCESS)

    if action == "password":
        current = request.POST.get("current_password", "")
        new = request.POST.get("new_password", "")
        if not user.verify_password(current):
            return json_response("当前密码错误")
        if len(new) < 8 or len(new) > 32:
            return json_response("新密码长度须为 8-32 位")
        user.set_password(new)
        user.save(update_fields=["password"])
        # 吊销全部登录会话之外的本会话继续有效（Django 语义：保持本会话）
        from django.contrib.auth import update_session_auth_hash

        update_session_auth_hash(request, user)
        # Yggdrasil 令牌全撤销（对照原版改密吊销语义）
        from yggdrasil.models import YggdrasilToken

        YggdrasilToken.objects.filter(owner=user).delete()
        return json_response("密码已更新", SUCCESS)

    if action == "email":
        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password", "")
        if "@" not in email:
            return json_response("邮箱格式不正确")
        from accounts.models import User

        if User.objects.filter(email=email).exists():
            return json_response("该邮箱已被使用")
        if not user.verify_password(password):
            return json_response("密码错误")
        user.email = email
        user.verified = False
        user.save(update_fields=["email", "verified"])
        return json_response("邮箱已更新，请重新验证", SUCCESS)

    if action == "delete":
        if user.is_admin():
            return json_response("管理员账号不能删除")
        if not user.verify_password(request.POST.get("password", "")):
            return json_response("密码错误")
        # 对照原版：删除级联（players 由 FK CASCADE 处理）
        auth_logout(request)
        user.delete()
        return json_response("账号已注销", SUCCESS)

    return json_response("未知的操作")


@login_required
@require_GET
def notifications_list(request):
    """三协商：浏览器直达=通知页 / AJAX=JSON。"""
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return render(
            request,
            "users/notifications.html",
            {"site_name": Option.objects.get_value("site_name")},
        )

    qs = request.user.notifications.all().order_by("-created_at")[:50]
    data = [
        {
            "id": n.id,
            "title": n.title,
            "content": n.content,
            "read": n.read_at is not None,
            "created_at": n.created_at.isoformat(),
        }
        for n in qs
    ]
    return json_response("", SUCCESS, data)


@login_required
@require_POST
def notification_read(request, notification_id: int):

    n = request.user.notifications.filter(id=notification_id).first()
    if n is None:
        return json_response("通知不存在")
    n.mark_as_read()
    return json_response("已标记已读", SUCCESS)


@login_required
@require_POST
def daily_sign(request):
    """每日签到（对照原版 sign：隔天可签，积分 score_per_sign）。"""
    user = request.user
    from datetime import timedelta

    from django.utils import timezone

    now = timezone.now()
    if user.last_sign_at and (now - user.last_sign_at) < timedelta(
        hours=20
    ):
        return json_response("今天已经签到过了")
    try:
        award = int(Option.objects.get_value("score_per_sign") or 0)
    except ValueError:
        award = 0
    user.score += award
    user.last_sign_at = now
    user.save(update_fields=["score", "last_sign_at"])
    return json_response("签到成功", SUCCESS, {"score": user.score})
