"""角色管理页视图：改名 / 换肤 / 换披风 / 删除（对照原版用户中心角色模态）。"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotFound
from django.shortcuts import render
from django.views.decorators.http import require_GET

from options.models import Option
from textures.models import Texture


def _default_texture(kind: str):
    """管理员指定的默认纹理占位（default_skin_tid / default_cape_tid）。"""
    try:
        tid = int(Option.objects.get_value(f"default_{kind}_tid") or 0)
    except ValueError:
        tid = 0
    return Texture.objects.filter(tid=tid).first() if tid else None


@login_required
@require_GET
def player_manage_page(request, pid: int):
    """角色管理页：信息 / 预览 / 改名 / 从衣柜应用皮肤披风 / 删除。"""
    player = request.user.players.select_related(
        "tid_skin", "tid_cape"
    ).filter(pid=pid).first()
    if player is None:
        return HttpResponseNotFound()

    closet = request.user.closet.select_related("texture").order_by("-added_at")
    closet_skins = [i.texture for i in closet if i.texture.type in ("steve", "alex")]
    closet_capes = [i.texture for i in closet if i.texture.type == "cape"]

    # 预览占位：无皮肤/披风时用默认纹理
    preview_skin = player.tid_skin or _default_texture("skin")
    preview_cape = player.tid_cape or _default_texture("cape")

    return render(
        request,
        "players/manage.html",
        {
            "player": player,
            "preview_skin": preview_skin,
            "preview_cape": preview_cape,
            "has_own_skin": player.tid_skin is not None,
            "has_own_cape": player.tid_cape is not None,
            "closet_skins": closet_skins,
            "closet_capes": closet_capes,
            "site_name": Option.objects.get_value("site_name"),
        },
    )
