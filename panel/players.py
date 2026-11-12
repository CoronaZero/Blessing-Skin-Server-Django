"""面板角色管理视图（全站角色列表与操作）。"""

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.views.decorators.http import require_GET

from config.jsonapi import SUCCESS, json_response
from options.models import Option
from players.models import Player

from .guards import admin_required


@admin_required
@require_GET
def players_page(request):
    q = (request.GET.get("q") or "").strip()
    qs = Player.objects.select_related("uid", "tid_skin", "tid_cape").order_by("-pid")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(uid__email__icontains=q) | Q(uid__nickname__icontains=q))
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))
    return render(
        request,
        "panel/players.html",
        {
            "active": "players",
            "is_super": request.user.permission >= 2,
            "page_obj": page,
            "q": q,
            "site_name": Option.objects.get_value("site_name") or "Blessing Skin",
        },
    )


@admin_required
def player_delete(request, pid: int):
    if request.method != "POST":
        return json_response("仅支持 POST", 1)
    from django.http import HttpResponseNotFound

    player = Player.objects.filter(pid=pid).first()
    if player is None:
        return HttpResponseNotFound()
    name = player.name
    owner_email = player.uid.email
    player.delete()
    return json_response(f"已删除角色 {name}（属于 {owner_email}）", SUCCESS)
