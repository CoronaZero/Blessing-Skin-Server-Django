"""oauth_bridge 视图：Microsoft OAuth 登录 / 注册引导 / 正版角色绑定 / 解绑。

流程一览（防账号接管核心：一律按 sub 显式绑定，绝不以 email 匹配账号）：
- GET  oauth/microsoft/login          发起登录；已登录用户转为「绑定 Microsoft 账号」
- GET  oauth/microsoft/genuine        发起正版角色绑定（Xbox 链路，需已登录）
- GET  oauth/microsoft/callback       Microsoft 统一回调（state 携带意图分派）
- GET/POST oauth/microsoft/choose     未绑定 MSA 首登引导页（绑定已有账号 / 创建新账号）
- POST oauth/microsoft/msa/unbind     解绑 Microsoft 账号（记冷却）
- POST oauth/microsoft/genuine/unbind 解绑正版角色（记冷却）
"""

import re
import time

from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from accounts.captcha import verify_captcha
from accounts.models import User
from config.jsonapi import FAILURE, SUCCESS, json_response
from options.models import Option

from . import msauth, options
from .models import MojangBinding, MsaIdentity, UnbindCooldown
from .state import pop_state, put_state
from .sync import sync_binding_textures

# 正版 profile id：32 位无连字符小写 hex
_MC_UUID = re.compile(r"^[0-9a-f]{32}$")
# pending 引导会话有效期（秒）
_PENDING_TTL = 600


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _safe_next(candidate: str) -> bool:
    return url_has_allowed_host_and_scheme(candidate, allowed_hosts=None)


def _redirect_uri(request) -> str:
    """回调地址：site_url 优先（与 Yggdrasil 材质 URL 同理，反代下 host 不可信）。"""
    configured = (Option.objects.get_value("site_url") or "").strip().rstrip("/")
    if configured:
        if not configured.startswith(("http://", "https://")):
            configured = "https://" + configured
        return configured + "/oauth/microsoft/callback"
    scheme = "https" if request.is_secure() else "http"
    return f"{scheme}://{request.get_host()}/oauth/microsoft/callback"


def _result(request, title: str, message: str, back_url: str = "/user"):
    """OAuth 流程结果页（消息不走 query 参数，防注入与钓鱼链接）。"""
    return render(
        request,
        "oauth_bridge/result.html",
        {
            "title": title,
            "message": message,
            "back_url": back_url,
            "site_name": Option.objects.get_value("site_name"),
        },
    )


def _cooldown_left(user, kind: str) -> int:
    return UnbindCooldown.remaining_seconds(
        user, kind, options.unbind_cooldown_days()
    )


def _fmt_remaining(seconds: int) -> str:
    if seconds >= 86400:
        return f"{(seconds + 86399) // 86400} 天"
    if seconds >= 3600:
        return f"{(seconds + 3599) // 3600} 小时"
    return f"{seconds} 秒"


# ---- 发起 ----


@require_GET
def msa_login(request):
    """发起登录授权；已登录用户转为绑定当前账号（同一入口两种意图）。"""
    if not options.msa_login_ready():
        return _result(
            request, "无法登录", "Microsoft 登录未启用或未配置", "/auth/login"
        )
    if request.user.is_authenticated:
        if MsaIdentity.objects.filter(user=request.user).exists():
            return _result(
                request, "已绑定",
                "当前账号已绑定 Microsoft 账号，如需更换请先在个人资料页解绑",
                "/user/profile",
            )
        if left := _cooldown_left(request.user, "msa"):
            return _result(
                request, "冷却期内",
                f"解绑冷却期内，{_fmt_remaining(left)}后可再次绑定 Microsoft 账号",
                "/user/profile",
            )
        intent = "bind"
    else:
        intent = "login"
    next_url = request.GET.get("next", "")
    if next_url and not _safe_next(next_url):
        next_url = ""
    state = put_state({"intent": intent, "next": next_url})
    return redirect(msauth.authorize_url(
        options.client_id(), _redirect_uri(request), msauth.SCOPE_LOGIN, state,
    ))


@require_GET
@login_required
def genuine_start(request):
    """发起正版角色绑定授权（scope: XboxLive.signin）。"""
    if not options.msa_bind_ready():
        return _result(
            request, "无法绑定", "正版角色绑定未启用或未配置", "/user/players"
        )
    if MojangBinding.objects.filter(user=request.user).exists():
        return _result(
            request, "已绑定",
            "当前账号已绑定正版角色，如需更换请先在「我的角色」页解绑",
            "/user/players",
        )
    if left := _cooldown_left(request.user, "genuine"):
        return _result(
            request, "冷却期内",
            f"解绑冷却期内，{_fmt_remaining(left)}后可再次绑定正版角色",
            "/user/players",
        )
    state = put_state({"intent": "genuine"})
    return redirect(msauth.authorize_url(
        options.client_id(), _redirect_uri(request), msauth.SCOPE_XBOX, state,
    ))


# ---- 回调 ----


@require_GET
def msa_callback(request):
    """Microsoft 统一回调：error / state 校验后按意图分派。"""
    if request.GET.get("error"):
        return _result(
            request, "授权失败",
            "Microsoft 授权被取消或失败，请重试或更换账号", "/auth/login",
        )
    code = request.GET.get("code") or ""
    payload = pop_state(request.GET.get("state") or "")
    if not code or payload is None:
        return _result(
            request, "授权失败",
            "授权状态已过期或无效，请重新发起登录/绑定", "/auth/login",
        )
    if payload.get("intent") == "genuine":
        return _genuine_callback(request, code)
    return _login_callback(request, payload, code)


def _login_callback(request, payload: dict, code: str):
    """登录/绑定意图：换 id_token 取 sub/email/name。"""
    try:
        identity = msauth.fetch_login_identity(
            options.client_id(), options.client_secret(), code,
            _redirect_uri(request),
        )
    except msauth.MicrosoftAuthError as exc:
        return _result(request, "授权失败", exc.message, "/auth/login")

    sub = identity["sub"]
    existing = MsaIdentity.objects.filter(sub=sub).select_related("user").first()

    if payload.get("intent") == "bind":
        user = request.user
        if not user.is_authenticated:
            return _result(
                request, "登录状态已失效",
                "绑定会话已失效，请重新登录后再次发起绑定", "/auth/login",
            )
        if existing is not None:
            return _result(
                request, "绑定失败",
                "该 Microsoft 账号已绑定其他站内账号", "/user/profile",
            )
        MsaIdentity.objects.create(
            user=user, sub=sub,
            email=identity.get("email", ""), display_name=identity.get("name", ""),
        )
        # 已登录绑定：直接回个人资料页（绑定状态区即反馈）
        return redirect("/user/profile")

    # 登录意图
    if existing is not None:
        user = existing.user
        if user.permission == User.Permission.BANNED:
            return _result(request, "无法登录", "该账号已被封禁", "/auth/login")
        auth_login(request, user)
        target = payload.get("next") or "/user"
        return redirect(target if target and _safe_next(target) else "/user")

    # 未绑定的 Microsoft 账号：进引导页决定绑定已有账号或创建新账号
    request.session["msa_pending"] = {
        "sub": sub,
        "email": identity.get("email", ""),
        "name": identity.get("name", ""),
        "next": payload.get("next") or "",
        "exp": int(time.time()) + _PENDING_TTL,
    }
    return redirect("oauth_bridge:choose")


def _genuine_callback(request, code: str):
    """正版绑定意图：Xbox 五步链路 → 建绑定（唯一性/冷却再校验，防时序绕过）。"""
    user = request.user
    if not user.is_authenticated:
        return _result(
            request, "登录状态已失效",
            "请重新登录后再次发起正版角色绑定", "/auth/login",
        )
    if not options.msa_bind_ready():
        return _result(request, "绑定失败", "正版角色绑定未启用或未配置", "/user/players")
    if MojangBinding.objects.filter(user=user).exists():
        return _result(
            request, "绑定失败",
            "当前账号已绑定正版角色，如需更换请先解绑", "/user/players",
        )
    if left := _cooldown_left(user, "genuine"):
        return _result(
            request, "冷却期内",
            f"解绑冷却期内，{_fmt_remaining(left)}后可再次绑定正版角色",
            "/user/players",
        )
    try:
        token = msauth.exchange_code(
            options.client_id(), options.client_secret(), code,
            _redirect_uri(request), msauth.SCOPE_XBOX,
        )
        profile, _mc_token = msauth.xbox_authenticate(token.get("access_token", ""))
    except msauth.MicrosoftAuthError as exc:
        return _result(request, "正版验证失败", exc.message, "/user/players")

    mc_uuid = str(profile.get("id") or "").replace("-", "").lower()
    mc_name = str(profile.get("name") or "")
    if not _MC_UUID.match(mc_uuid) or not mc_name:
        return _result(
            request, "正版验证失败",
            "Minecraft 角色信息异常，请稍后重试", "/user/players",
        )
    if MojangBinding.objects.filter(mc_uuid=mc_uuid).exists():
        return _result(
            request, "绑定失败",
            "该正版角色已被其他站内账号绑定", "/user/players",
        )
    binding = MojangBinding.objects.create(user=user, mc_uuid=mc_uuid, mc_name=mc_name)
    # 纹理同步失败不阻塞绑定（透传时会再补）
    sync_binding_textures(binding, profile)
    return _result(
        request, "绑定成功",
        f"正版角色「{mc_name}」绑定成功，可在「我的角色」页管理",
        "/user/players",
    )


# ---- 引导页（未绑定 MSA 首登）----


def _pending_valid(request) -> dict | None:
    pending = request.session.get("msa_pending")
    if not isinstance(pending, dict):
        return None
    if pending.get("exp", 0) < time.time() or not pending.get("sub"):
        request.session.pop("msa_pending", None)
        return None
    return pending


@ensure_csrf_cookie
def msa_choose(request):
    """引导页：匿名可达（首登场景）；含表单须保证 csrftoken cookie。"""
    if request.method == "GET":
        return _choose_page(request)
    return _choose_submit(request)


def _choose_page(request):
    pending = _pending_valid(request)
    if pending is None:
        return _result(
            request, "会话过期",
            "引导会话已过期，请重新发起 Microsoft 登录", "/auth/login",
        )
    from accounts.captcha import get_captcha_requirements

    register_open = (Option.objects.get_value("disable_registration") or "0") not in (
        "1", "true", "on",
    )
    invitation_required = (Option.objects.get_value("invitation_required") or "0") in (
        "1", "true",
    )
    return render(
        request,
        "oauth_bridge/choose.html",
        {
            "msa_email": pending.get("email", ""),
            "msa_name": pending.get("name", ""),
            "can_create": options.msa_register_ready() and register_open,
            "invitation_required": invitation_required,
            "captcha": get_captcha_requirements(),
            "site_name": Option.objects.get_value("site_name"),
        },
    )


def _choose_submit(request):
    pending = _pending_valid(request)
    if pending is None:
        return json_response("引导会话已过期，请重新发起 Microsoft 登录", FAILURE)
    action = request.POST.get("action", "")
    if action == "bind":
        return _choose_bind(request, pending)
    if action == "create":
        return _choose_create(request, pending)
    return json_response("未知的操作", FAILURE)


def _choose_bind(request, pending: dict):
    """绑定已有账号：站内凭据验证（密码）后落绑定关系。"""
    email = (request.POST.get("email") or "").strip()
    password = request.POST.get("password", "")
    if not email or not password:
        return json_response("请填写站内邮箱与密码", FAILURE)
    user = authenticate(request, username=email, password=password)
    if user is None:
        return json_response("站内邮箱或密码错误", FAILURE)
    if user.permission == User.Permission.BANNED:
        return json_response("该账号已被封禁", FAILURE)
    if MsaIdentity.objects.filter(sub=pending["sub"]).exists():
        return json_response("该 Microsoft 账号刚被其他账号绑定，请重试", FAILURE)
    MsaIdentity.objects.create(
        user=user, sub=pending["sub"],
        email=pending.get("email", ""), display_name=pending.get("name", ""),
    )
    request.session.pop("msa_pending", None)
    auth_login(request, user)
    target = pending.get("next") or "/user"
    return json_response(
        "绑定成功，已登录", SUCCESS,
        {"redirectTo": target if _safe_next(target) else "/user"},
    )


def _choose_create(request, pending: dict):
    """创建新账号：须过全部注册限制（站方注册开关/验证码/邀请码/同 IP 上限）。"""
    if not options.msa_register_ready():
        return json_response("站点未开放 Microsoft 注册", FAILURE)
    if (Option.objects.get_value("disable_registration") or "0") in ("1", "true", "on"):
        return json_response("本站已关闭注册", FAILURE)
    if not verify_captcha(request.POST.get("captcha"), ip=_client_ip(request)):
        return json_response("人机验证未通过", FAILURE)
    email = pending.get("email", "")
    if not email or "@" not in email:
        return json_response("Microsoft 账号未提供邮箱，无法创建账号", FAILURE)
    if User.objects.filter(email=email).exists():
        return json_response(
            "该邮箱已被注册，请返回选择「绑定已有账号」", FAILURE,
        )

    invitation = None
    if (Option.objects.get_value("invitation_required") or "0") in ("1", "true"):
        from invitation.models import InvitationCode

        code_value = (request.POST.get("invitation_code") or "").strip()
        invitation = InvitationCode.objects.filter(
            code=code_value, used_by__isnull=True
        ).first()
        if invitation is None:
            return json_response("邀请码无效或已被使用", FAILURE)

    ip = _client_ip(request)
    try:
        regs_limit = int(Option.objects.get_value("regs_per_ip") or 3)
    except ValueError:
        regs_limit = 3
    if User.objects.filter(ip=ip).count() >= regs_limit:
        return json_response(f"该 IP 注册数已达上限（{regs_limit}）", FAILURE)

    nickname = (pending.get("name") or email.split("@")[0])[:50]
    # Microsoft 邮箱经微软验证，站内直接视为已验证；无密码（usable_password=False）
    user = User.objects.create_user(
        email=email, nickname=nickname, password=None, ip=ip, verified=True,
    )
    try:
        initial_score = int(Option.objects.get_value("user_initial_score") or 0)
    except ValueError:
        initial_score = 0
    if initial_score:
        user.score = initial_score
        user.save(update_fields=["score"])
    if invitation is not None:
        from django.utils import timezone as _tz

        invitation.used_by = user
        invitation.used_at = _tz.now()
        invitation.save(update_fields=["used_by", "used_at"])
    MsaIdentity.objects.create(
        user=user, sub=pending["sub"],
        email=email, display_name=pending.get("name", ""),
    )
    request.session.pop("msa_pending", None)
    auth_login(request, user)
    return json_response(
        "注册成功", SUCCESS,
        {"redirectTo": pending.get("next") or "/user"},
    )


# ---- 解绑 ----


@login_required
@require_POST
def msa_unbind(request):
    row = MsaIdentity.objects.filter(user=request.user).first()
    if row is None:
        return json_response("当前账号未绑定 Microsoft 账号", FAILURE)
    row.delete()
    UnbindCooldown.record(request.user, "msa")
    return json_response("已解绑 Microsoft 账号（进入冷却期）", SUCCESS)


@login_required
@require_POST
def genuine_unbind(request):
    row = MojangBinding.objects.filter(user=request.user).first()
    if row is None:
        return json_response("当前账号未绑定正版角色", FAILURE)
    row.delete()
    UnbindCooldown.record(request.user, "genuine")
    return json_response("已解绑正版角色（进入冷却期）", SUCCESS)
