"""面板邀请码管理页视图。"""

from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from config.jsonapi import SUCCESS, json_response
from invitation.models import InvitationCode

from .guards import super_admin_required


@super_admin_required
@require_GET
def invitations_page(request):
    qs = InvitationCode.objects.select_related("used_by").order_by("-generated_at")
    paginator = Paginator(qs, 30)
    page = paginator.get_page(request.GET.get("page", 1))
    from options.models import Option

    return render(
        request,
        "panel/invitations.html",
        {
            "active": "invitations",
            "is_super": True,
            "page_obj": page,
            "site_name": Option.objects.get_value("site_name") or "Blessing Skin",
        },
    )


@super_admin_required
@require_POST
def invitations_generate(request):
    """批量生成邀请码（默认 10 个）。"""
    try:
        amount = int(request.POST.get("amount", "10"))
    except ValueError:
        amount = 10
    amount = max(1, min(amount, 100))  # 上限防滥用
    for _ in range(amount):
        InvitationCode.objects.create()
    return json_response(f"已生成 {amount} 个邀请码", SUCCESS)
