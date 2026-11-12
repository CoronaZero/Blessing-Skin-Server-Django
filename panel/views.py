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
from options.models import Option
from players.models import Player
from textures.models import ClosetItem, Texture

from .captcha_test import captcha_test  # noqa: F401  re-export for urls
from .guards import _is_super, admin_required, super_admin_required
from .invitations import invitations_generate, invitations_page  # noqa: F401
from .mail_test import mail_test  # noqa: F401  re-export for urls
from .players import player_delete, players_page  # noqa: F401
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
    action = request.POST.get("action")
    if action == "ban":
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
    from textures.storage import texture_storage

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
        Player.objects.filter(tid_skin=target).update(tid_skin=None)
        Player.objects.filter(tid_cape=target).update(tid_cape=None)
        ClosetItem.objects.filter(texture=target).delete()
        Report.objects.filter(texture=target).delete()
        hash_value = target.hash
        target.delete()
        if not Texture.objects.filter(hash=hash_value).exists():
            try:
                texture_storage.delete(hash_value)
            except Exception:  # noqa: BLE001
                pass
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
    })
    return render(request, "panel/options.html", ctx)


@super_admin_required
@require_POST
def options_save(request):
    known = {f.key for g in OPTION_REGISTRY for f in g.fields}
    widget_of = {f.key: f.widget for g in OPTION_REGISTRY for f in g.fields}
    updated = 0
    for key, value in request.POST.items():
        if key not in known:
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
            if widget == "password" and value.strip() == "":
                continue
            Option.objects.set(key, value.strip())
        updated += 1
    return json_response(f"已保存 {updated} 项", SUCCESS)


def _derive_public_key() -> str:
    private_pem = (Option.objects.get_value("ygg_private_key") or "").strip()
    if not private_pem:
        return ""
    try:
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_private_key(private_pem.encode(), password=None)
        return key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
    except (ValueError, TypeError):
        return ""


# ---- 广播（仅超管）----


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
