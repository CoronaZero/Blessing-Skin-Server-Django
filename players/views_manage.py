"""角色管理页视图：改名 / 换肤 / 换披风 / 删除（对照原版用户中心角色模态）。"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotFound
from django.shortcuts import render
from django.views.decorators.http import require_GET

from options.models import Option
from textures.models import Texture


@login_required
@require_GET
def player_manage_page(request, pid: int):
    """角色管理页：信息 / 预览 / 改名 / 从衣柜应用皮肤披风 / 删除。"""
    player = request.user.players.select_related(
        "tid_skin", "tid_cape"
    ).filter(pid=pid).first()
    if player is None:
        return HttpResponseNotFound()

    # 衣柜可选材质（按类型分组）
    closet = (
        request.user.closet.select_related("texture")
        .order_by("-added_at")
    )
    closet_skins = [item.texture for item in closet
                    if item.texture.type in ("steve", "alex")]
    closet_capes = [item.texture for item in closet if item.texture.type == "cape"]

    return render(
        request,
        "players/manage.html",
        {
            "player": player,
            "closet_skins": closet_skins,
            "closet_capes": closet_capes,
            "site_name": Option.objects.get_value("site_name"),
            "player_name_rule": Option.objects.get_value("player_name_rule") or "official",
        },
    )


def _texture_card_data(texture: Texture) -> dict:
    return {
        "tid": texture.tid,
        "name": texture.name,
        "hash": texture.hash,
        "type": texture.type,
    }
