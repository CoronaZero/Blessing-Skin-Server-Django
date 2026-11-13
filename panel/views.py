"""自研管理面板：概览/用户/皮肤/举报/选项/广播。

对照原版后台的功能分区。权限模型对齐原版：
- ADMIN（1）：概览/用户管理/皮肤库管理/举报处理
- SUPER_ADMIN（2）：以上 + 站点选项/默认材质/Yggdrasil/邮件/验证码/广播
自带 django.contrib.admin 不再挂路由。
"""

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User
from config.jsonapi import FAILURE, SUCCESS, json_response
from moderation.models import Report
from notifications.models import Notification
from oauth_bridge.models import MojangBinding, UnbindCooldown
from options.models import Option
from players.models import Player
from textures.models import Texture
from yggdrasil.models import YggdrasilLog

from .captcha_test import captcha_test  # noqa: F401  re-export for urls
from .guards import _is_super, admin_required, super_admin_required
from .invitations import invitations_generate, invitations_page  # noqa: F401
from .mail_test import mail_test  # noqa: F401  re-export for urls
from .players import player_delete, player_set_texture, players_page  # noqa: F401
from .registry import OPTION_REGISTRY


def _panel_ctx(request, active):
    return {
        "active": active,
        "is_super": _is_super(request.user),
        "site_name": Option.objects.get_value("site_name") or "Blessing Skin",
    }


# ---- 概览 ----


@admin_required
@require_GET
def dashboard(request):
    ctx = _panel_ctx(request, "dashboard")
    ctx.update({
        "user_count": User.objects.count(),
        "player_count": Player.objects.count(),
        "texture_count": Texture.objects.count(),
        "report_pending": Report.objects.filter(status=Report.Status.PENDING).count(),
        "recent_users": User.objects.order_by("-uid")[:8],
    })
    return render(request, "panel/dashboard.html", ctx)


# ---- 用户管理 ----


@admin_required
@require_GET
def users(request):
    q = (request.GET.get("q") or "").strip()
    qs = User.objects.order_by("-uid")
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(nickname__icontains=q))
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))
    ctx = _panel_ctx(request, "users")
    ctx.update({"page_obj": page, "q": q})
    return render(request, "panel/users.html", ctx)


@admin_required
@require_POST
def user_action(request, uid: int):
    target = User.objects.filter(uid=uid).first()
    if target is None:
        return json_response("用户不存在", FAILURE)
    # 权限层级：普通管理员不能操作管理员/超管，超管不能操作超管同级以外的自己；
    # 保护规则：任何人都不能封禁/降级自己，普通管理员不能触碰其他管理员
    actor = request.user
    if target.uid == actor.uid and request.POST.get("action") in (
        "ban", "perm", "reset_password", "unverify",
    ):
        return json_response("不能对自己执行此操作", FAILURE)
    if (
        not _is_super(actor)
        and target.permission >= User.Permission.ADMIN
    ):
        return json_response(
            "普通管理员不能操作其他管理员或超级管理员", FAILURE
        )
    if (
        _is_super(actor)
        and target.permission == User.Permission.SUPER_ADMIN
        and target.uid != actor.uid
    ):
        return json_response("不能操作其他超级管理员", FAILURE)
    action = request.POST.get("action")
    if action == "ban":
        if target.permission >= User.Permission.SUPER_ADMIN:
            return json_response("不能封禁超级管理员", FAILURE)
        target.permission = User.Permission.BANNED
        target.save(update_fields=["permission"])
        return json_response("已封禁", SUCCESS)
    if action == "unban":
        target.permission = User.Permission.NORMAL
        target.save(update_fields=["permission"])
        return json_response("已解封", SUCCESS)
    if action == "score":
        try:
            delta = int(request.POST.get("delta", "0"))
        except ValueError:
            return json_response("积分须为数字", FAILURE)
        target.score = max(0, target.score + delta)
        target.save(update_fields=["score"])
        return json_response(f"积分已调整（{delta:+d}）", SUCCESS)
    if action == "reset_password":
        new_pwd = (request.POST.get("password") or "").strip()
        if len(new_pwd) < 8 or len(new_pwd) > 32:
            return json_response("新密码须 8-32 位", FAILURE)
        target.set_password(new_pwd)
        target.save(update_fields=["password"])
        return json_response("密码已重置", SUCCESS)
    if action == "verify":
        target.verified = True
        target.save(update_fields=["verified"])
        return json_response("已标记邮箱验证通过", SUCCESS)
    if action == "unverify":
        target.verified = False
        target.save(update_fields=["verified"])
        return json_response("已取消邮箱验证", SUCCESS)
    if action == "perm" and _is_super(request.user):
        try:
            level = int(request.POST.get("level", "0"))
        except ValueError:
            return json_response("权限级别无效", FAILURE)
        if level not in tuple(User.Permission.values):
            return json_response("权限级别无效", FAILURE)
        if target.uid == request.user.uid and level < User.Permission.SUPER_ADMIN:
            return json_response("不能降级自己", FAILURE)
        target.permission = level
        target.save(update_fields=["permission"])
        return json_response("权限已更新", SUCCESS)
    return json_response("未知操作", FAILURE)


# ---- 皮肤库管理 ----


@admin_required
@require_GET
def textures(request):
    q = (request.GET.get("q") or "").strip()
    qs = Texture.objects.select_related("uploader").order_by("-tid")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(hash__icontains=q))
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))
    ctx = _panel_ctx(request, "textures")
    ctx.update({"page_obj": page, "q": q})
    return render(request, "panel/textures.html", ctx)


@admin_required
@require_POST
def texture_action(request, tid: int):

    target = Texture.objects.filter(tid=tid).first()
    if target is None:
        return json_response("纹理不存在", FAILURE)
    action = request.POST.get("action")
    if action == "toggle_public":
        target.public = not target.public
        target.save(update_fields=["public"])
        return json_response("已切换公开性", SUCCESS)
    if action == "rename":
        name = (request.POST.get("name") or "").strip()[:50]
        if not name:
            return json_response("名称不能为空", FAILURE)
        target.name = name
        target.save(update_fields=["name"])
        return json_response("已重命名", SUCCESS)
    if action == "delete":
        target.delete_with_cleanup()
        return json_response("已删除", SUCCESS)
    return json_response("未知操作", FAILURE)


# ---- 举报处理 ----


@admin_required
@require_GET
def reports(request):
    qs = Report.objects.select_related("texture", "uploader", "reporter").order_by("-id")
    status = request.GET.get("status")
    if status in ("0", "1", "2"):
        qs = qs.filter(status=int(status))
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))
    ctx = _panel_ctx(request, "reports")
    ctx.update({"page_obj": page, "status": status or ""})
    return render(request, "panel/reports.html", ctx)


@admin_required
@require_POST
def report_action(request, rid: int):
    target = Report.objects.filter(id=rid).first()
    if target is None:
        return json_response("举报不存在", FAILURE)
    action = request.POST.get("action")
    if action == "resolve":
        target.status = Report.Status.RESOLVED
        target.save(update_fields=["status"])
        return json_response("已处理", SUCCESS)
    if action == "reject":
        target.status = Report.Status.REJECTED
        target.save(update_fields=["status"])
        return json_response("已驳回", SUCCESS)
    return json_response("未知操作", FAILURE)


# ---- Yggdrasil 日志（仅超管，对照原版 ConfigController::logPage）----


# 动作中文名映射（原版 trans('Yggdrasil::log.actions')）
LOG_ACTION_LABELS = {
    "authenticate": "登录",
    "auth_failed": "登录失败",
    "refresh": "刷新令牌",
    "validate": "校验令牌",
    "invalidate": "吊销令牌",
    "signout": "退出登录",
    "join": "加入服务器",
    "has_joined": "服务器验证",
    "upload_skin": "上传皮肤",
    "reset_skin": "重置皮肤",
    "upload_cape": "上传披风",
    "reset_cape": "重置披风",
}


@super_admin_required
@require_GET
def yggdrasil_logs(request):
    qs = (
        YggdrasilLog.objects.select_related("user", "player")
        .order_by("-time", "-id")
    )
    action = (request.GET.get("action") or "").strip()
    if action:
        qs = qs.filter(action=action)
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))
    ctx = _panel_ctx(request, "yggdrasil_logs")
    ctx.update({
        "page_obj": page,
        "action": action,
        "action_labels": LOG_ACTION_LABELS,
    })
    return render(request, "panel/yggdrasil_logs.html", ctx)


# ---- 正版角色绑定管理（管理员+超管；角色为只读，仅查看与解绑）----


@admin_required
@require_GET
def mojang_bindings(request):
    q = (request.GET.get("q") or "").strip()
    qs = MojangBinding.objects.select_related("user").order_by("-id")
    if q:
        qs = qs.filter(
            Q(mc_name__icontains=q)
            | Q(mc_uuid__icontains=q)
            | Q(user__email__icontains=q)
            | Q(user__nickname__icontains=q)
        )
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))
    ctx = _panel_ctx(request, "mojang_bindings")
    ctx.update({"page_obj": page, "q": q})
    return render(request, "panel/mojang_bindings.html", ctx)


@admin_required
@require_POST
def mojang_binding_unbind(request, bid: int):
    row = MojangBinding.objects.select_related("user").filter(id=bid).first()
    if row is None:
        return json_response("绑定记录不存在", FAILURE)
    owner = row.user
    mc_name = row.mc_name
    row.delete()
    # 与用户自助解绑同一规则：记冷却（防绕过冷却反复横跳）
    UnbindCooldown.record(owner, "genuine")
    return json_response(
        f"已解绑 {owner.nickname or owner.email} 的正版角色「{mc_name}」",
        SUCCESS,
    )


# ---- 选项面板（仅超管）----


@super_admin_required
@require_GET
def options_page(request, section: str = "general"):
    """选项面板（按 section 分页签渲染，拆分四页不再杂糅）。"""
    from .registry import OPTION_SECTIONS

    if section not in {s[0] for s in OPTION_SECTIONS}:
        section = "general"
    groups = []
    for group in OPTION_REGISTRY:
        if group.section != section:
            continue
        values = {f.key: Option.objects.get_value(f.key, "") for f in group.fields}
        groups.append({"group": group, "values": values})
    ctx = _panel_ctx(request, "options")
    ctx.update({
        "option_groups": groups,
        "sections": OPTION_SECTIONS,
        "current_section": section,
        "ygg_public_key": _derive_public_key(),
        "ygg_key_bits": _ygg_key_bits(),
    })
    return render(request, "panel/options.html", ctx)


@super_admin_required
@require_POST
def options_save(request):
    known = {f.key for g in OPTION_REGISTRY for f in g.fields}
    widget_of = {f.key: f.widget for g in OPTION_REGISTRY for f in g.fields}
    # Yggdrasil 私钥：非空须为合法 PEM（对照原版 openssl_pkey_get_private 校验），
    # 保存后同步派生公钥 option；留空不修改（与 password 控件同语义）
    new_key = (request.POST.get("ygg_private_key") or "").strip()
    if new_key:
        derived = _derive_public_key(new_key)
        if not derived:
            return json_response("签名私钥 PEM 格式无效，未保存", FAILURE)
        Option.objects.set("ygg_private_key", new_key)
        Option.objects.set("ygg_public_key", derived)
    updated = 0
    for key, value in request.POST.items():
        if key not in known or key == "ygg_private_key":
            continue
        widget = widget_of[key]
        if widget == "bool":
            Option.objects.set(key, "1" if value in ("1", "on", "true") else "0")
        elif widget == "int":
            try:
                Option.objects.set(key, str(max(0, int(value))))
            except ValueError:
                continue
        else:
            if widget in ("password", "secret_textarea") and value.strip() == "":
                continue
            Option.objects.set(key, value.strip())
        updated += 1
    return json_response(f"已保存 {updated} 项", SUCCESS)


def _derive_public_key(pem: str = "") -> str:
    """从 PEM 私钥派生公钥；不传则读当前 option。无效返回空串。"""
    if not pem:
        pem = (Option.objects.get_value("ygg_private_key") or "").strip()
    if not pem:
        return ""
    try:
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_private_key(pem.encode(), password=None)
        return key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
    except (ValueError, TypeError):
        return ""


def _ygg_key_bits() -> int:
    """当前私钥位数（未配置/无效返回 0，用于面板状态提示）。"""
    pem = (Option.objects.get_value("ygg_private_key") or "").strip()
    if not pem:
        return 0
    try:
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_private_key(pem.encode(), password=None)
        return int(getattr(key, "key_size", 0))
    except (ValueError, TypeError):
        return 0


# ---- 广播（仅超管）----


@super_admin_required
@require_POST
def ygg_key_generate(request):
    """重新生成 4096 位 Yggdrasil 签名密钥对（对照原版 ConfigController::generate）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    Option.objects.set("ygg_private_key", private_pem)
    Option.objects.set("ygg_public_key", public_pem)
    return json_response("已生成新的 4096 位密钥对", SUCCESS)


@super_admin_required
@require_POST
def broadcast(request):
    title = (request.POST.get("title") or "").strip()
    content = (request.POST.get("content") or "").strip()
    if not title:
        return json_response("标题不能为空", FAILURE)
    count = 0
    for user in User.objects.filter(permission__gte=User.Permission.NORMAL):
        Notification.objects.create(recipient=user, title=title, content=content)
        count += 1
    return json_response(f"已向 {count} 位用户发送", SUCCESS)


def legacy_admin_redirect(request):
    return redirect("panel:dashboard")
