"""面板角色管理视图（全站角色列表与操作）。"""

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseNotFound
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User
from config.jsonapi import FAILURE, SUCCESS, json_response
from options.models import Option
from players.models import Player
from textures.models import Texture

from .guards import admin_required


@admin_required
@require_GET
def players_page(request):
    q = (request.GET.get("q") or "").strip()
    qs = Player.objects.select_related("uid", "tid_skin", "tid_cape").order_by("-pid")
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(uid__email__icontains=q)
            | Q(uid__nickname__icontains=q)
        )
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


def _can_operate(request, player) -> "str|None":
    """普通管理员不能操作管理员/超管的角色。"""
    owner = player.uid
    if request.user.permission < User.Permission.SUPER_ADMIN and owner.is_admin():
        return "普通管理员不能操作管理员或超级管理员的角色"
    if owner.permission == User.Permission.SUPER_ADMIN and owner.uid != request.user.uid:
        return "不能操作超级管理员的角色"
    return None


@admin_required
@require_POST
def player_delete(request, pid: int):
    player = Player.objects.filter(pid=pid).select_related("uid").first()
    if player is None:
        return HttpResponseNotFound()
    if err := _can_operate(request, player):
        return json_response(err, FAILURE)
    name = player.name
    owner_email = player.uid.email
    player.delete()
    return json_response(f"已删除角色 {name}（属于 {owner_email}）", SUCCESS)


@admin_required
@require_POST
def player_set_texture(request, pid: int, kind: str):
    """管理员设置/清除角色皮肤或披风（kind: skin/cape）。"""
    if kind not in ("skin", "cape"):
        return json_response("未知材质类型", FAILURE)
    player = Player.objects.filter(pid=pid).select_related("uid").first()
    if player is None:
        return json_response("角色不存在", FAILURE)
    if err := _can_operate(request, player):
        return json_response(err, FAILURE)
    tid = (request.POST.get("tid") or "").strip()
    if tid in ("", "0"):
        setattr(player, f"tid_{kind}", None)
        player.save(update_fields=[f"tid_{kind}"])
        return json_response("已清除", SUCCESS)
    try:
        texture = Texture.objects.get(tid=int(tid))
    except (ValueError, Texture.DoesNotExist):
        return json_response("纹理不存在", FAILURE)
    expected = "cape" if kind == "cape" else ("steve", "alex")
    if texture.type != expected and not (
        kind == "skin" and texture.type in ("steve", "alex")
    ):
        return json_response(f"类型不符：需要{'披风' if kind == 'cape' else '皮肤'}", FAILURE)
    setattr(player, f"tid_{kind}", texture)
    player.save(update_fields=[f"tid_{kind}"])
    return json_response(f"已设置{ '披风' if kind == 'cape' else '皮肤' }：{texture.name}", SUCCESS)
