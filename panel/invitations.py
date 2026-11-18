"""面板邀请码管理页视图。"""

from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from config.jsonapi import SUCCESS, json_response
from invitation.models import InvitationCode

from .guards import _panel_ctx, admin_required


@admin_required
@require_GET
def invitations_page(request):
    qs = InvitationCode.objects.select_related(
        "used_by", "generated_by"
    ).order_by("-generated_at")
    paginator = Paginator(qs, 30)
    page = paginator.get_page(request.GET.get("page", 1))
    ctx = _panel_ctx(request, "invitations")
    ctx.update({"page_obj": page})
    return render(request, "panel/invitations.html", ctx)


@admin_required
@require_POST
def invitations_generate(request):
    """批量生成邀请码（默认 10 个）。"""
    try:
        amount = int(request.POST.get("amount", "10"))
    except ValueError:
        amount = 10
    amount = max(1, min(amount, 100))  # 上限防滥用
    for _ in range(amount):
        InvitationCode.objects.create(generated_by=request.user)
    return json_response(f"已生成 {amount} 个邀请码", SUCCESS)
