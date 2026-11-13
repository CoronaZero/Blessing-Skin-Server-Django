"""teximport 视图：用户提交导入 + 管理面板（审核/队列/最近）。"""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from config.jsonapi import FAILURE, SUCCESS, json_response
from options.models import Option
from panel.guards import admin_required

from . import fetcher
from .models import ImportRequest
from .worker import ensure_worker, interval_seconds, score_multiplier

# 每用户进行中（待审核/待拉取/拉取中）请求上限，防滥用刷请求
ACTIVE_LIMIT_PER_USER = 5


def _tri_state() -> str:
    return (Option.objects.get_value("import_enabled") or "0").strip().lower()


@login_required
@require_POST
def submit_import(request):
    """提交站外纹理导入（URL 入队）。"""
    if _tri_state() not in ("1", "true", "on"):
        return json_response("管理员未开放站外导入功能")

    raw_url = (request.POST.get("url") or "").strip()
    if not raw_url:
        return json_response("请填写皮肤站链接")

    # 追踪参数剥离（入队前完成，落库与详情页展示的均为净化链接）
    sanitized = fetcher.sanitize_url(raw_url)
    if not sanitized:
        return json_response("无法解析该链接")
    try:
        site, tid = fetcher.parse_target(sanitized)
    except fetcher.FetchError as exc:
        return json_response(str(exc))
    # 禁止目标为本站：自导入语义无效，且可借本站 info 接口绕过站内逻辑
    from urllib.parse import urlsplit

    target_host = (urlsplit(site).hostname or "").lower()
    self_hosts = {request.get_host().split(":")[0].lower()}
    configured = (Option.objects.get_value("site_url") or "").strip()
    if configured:
        own = urlsplit(
            configured if "://" in configured else f"https://{configured}"
        ).hostname
        if own:
            self_hosts.add(own.lower())
    if target_host in self_hosts:
        return json_response("不能从本站导入纹理，请直接在皮肤库收藏")
    # SSRF 静态预检（DNS 层面的校验留到 worker 实际拉取时）
    try:
        fetcher.assert_safe_host(site.split("://", 1)[1])
    except fetcher.FetchError as exc:
        return json_response(str(exc))

    # 同链接重复入队与进行中数量限制
    if ImportRequest.objects.filter(
        requester=request.user, source_url=sanitized,
        status__in=(ImportRequest.Status.PENDING, ImportRequest.Status.QUEUED,
                    ImportRequest.Status.PROCESSING),
    ).exists():
        return json_response("该链接已在处理中")
    active = ImportRequest.objects.filter(
        requester=request.user,
        status__in=(ImportRequest.Status.PENDING, ImportRequest.Status.QUEUED,
                    ImportRequest.Status.PROCESSING),
    ).count()
    if active >= ACTIVE_LIMIT_PER_USER:
        return json_response(f"你已有 {active} 个进行中的导入请求，请等待完成后再提交")

    require_review = (
        Option.objects.get_value("import_require_review") or "0"
    ).strip().lower() in ("1", "true", "on")
    # 可见性单选（默认私密；与上传页 public 复选框语义对齐）
    requested_public = request.POST.get("public") in ("1", "true", "on")

    ImportRequest.objects.create(
        requester=request.user,
        source_url=sanitized,
        site=site,
        tid=tid,
        requested_public=requested_public,
        status=(ImportRequest.Status.PENDING if require_review
                else ImportRequest.Status.QUEUED),
    )
    visibility = "公开" if requested_public else "私密"
    if require_review:
        from notifications.models import Notification

        Notification.objects.create(
            recipient=request.user,
            title="导入请求已提交",
            content=f"「{sanitized}」（{visibility}）已进入审核队列，"
                    "管理员通过后将自动拉取。",
        )
        return json_response("已提交，等待管理员审核", SUCCESS)
    ensure_worker()
    return json_response(
        f"已加入拉取队列（{visibility}），完成后将发站内通知",
        SUCCESS, {"position": "queued"},
    )


# ---- 管理面板 ----


def _panel_ctx(request, active):
    from panel.views import _panel_ctx as base_ctx

    return base_ctx(request, active)


@admin_required
@require_GET
def imports_page(request):
    """导入管理：待审核 / 队列中 / 最近结果。"""
    status = (request.GET.get("status") or "pending").strip()
    if status not in ("pending", "queued", "recent"):
        status = "pending"

    if status == "pending":
        qs = ImportRequest.objects.select_related("requester").filter(
            status=ImportRequest.Status.PENDING,
        )
    elif status == "queued":
        qs = ImportRequest.objects.select_related("requester").filter(
            status__in=(ImportRequest.Status.QUEUED, ImportRequest.Status.PROCESSING),
        )
    else:
        qs = ImportRequest.objects.select_related(
            "requester", "texture",
        ).filter(
            status__in=(ImportRequest.Status.DONE, ImportRequest.Status.DEDUP,
                        ImportRequest.Status.FAILED, ImportRequest.Status.REJECTED),
        )

    pending_count = ImportRequest.objects.filter(status=ImportRequest.Status.PENDING).count()
    queued_count = ImportRequest.objects.filter(
        status__in=(ImportRequest.Status.QUEUED, ImportRequest.Status.PROCESSING),
    ).count()

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))
    ctx = _panel_ctx(request, "imports")
    ctx.update({
        "page_obj": page,
        "status": status,
        "pending_count": pending_count,
        "queued_count": queued_count,
        "interval": interval_seconds(),
        "multiplier": score_multiplier(),
    })
    return render(request, "panel/imports.html", ctx)


@admin_required
@require_POST
def import_action(request, item_id: int):
    """审核动作：approve（通过入队）/ reject（拒绝）。"""
    item = ImportRequest.objects.select_related("requester").filter(
        id=item_id, status=ImportRequest.Status.PENDING,
    ).first()
    if item is None:
        return json_response("请求不存在或已处理", FAILURE)
    action = request.POST.get("action")
    if action == "approve":
        item.status = ImportRequest.Status.QUEUED
        item.save(update_fields=["status"])
        ensure_worker()
        return json_response("已通过并加入拉取队列", SUCCESS)
    if action == "reject":
        item.status = ImportRequest.Status.REJECTED
        item.save(update_fields=["status"])
        from notifications.models import Notification

        Notification.objects.create(
            recipient=item.requester,
            title="导入请求被拒绝",
            content=f"「{item.source_url}」未通过管理员审核。",
        )
        return json_response("已拒绝", SUCCESS)
    return json_response("未知操作", FAILURE)
