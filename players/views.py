"""players 视图：用户角色管理。"""

from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST

from config.jsonapi import FAILURE, SUCCESS, json_response
from options.models import Option
from players.models import Player

from .uuidgen import generate_uuid_by_option, generate_uuid_v3


def _bool_option(name: str, default: str = "0") -> bool:
    return (Option.objects.get_value(name) or default).lower() in ("1", "true", "on")


def _int_option(name: str, default: int = 0) -> int:
    try:
        return int(Option.objects.get_value(name) or default)
    except ValueError:
        return default


def apply_default_textures(player) -> None:
    """创建角色时自动应用管理员指定的默认皮肤/披风。"""
    from textures.models import Texture

    for kind in ("skin", "cape"):
        tid = _int_option(f"default_{kind}_tid", 0)
        if not tid:
            continue
        texture = Texture.objects.filter(tid=tid).first()
        if texture:
            setattr(player, f"tid_{kind}", texture)
    player.save()


def validate_player_name(name: str) -> str | None:
    """角色名规则（对照原版 player_name_rule 四档）。

    默认 official；cjk/utf8 允许中文；custom 读正则 option。
    返回错误消息或 None（合法）。
    """
    if not name:
        return "角色名不能为空"
    if len(name) > 50:
        return "角色名过长"
    rule = (Option.objects.get_value("player_name_rule") or "official").lower()

    if rule == "utf8":
        if any(c.isspace() for c in name):
            return "角色名不能包含空白字符"
        return None

    import re

    if rule == "official":
        pattern = r"^[A-Za-z0-9_]+$"
    elif rule == "cjk":
        pattern = r"^[A-Za-z0-9_§\u4e00-\u9fff]+$"
    elif rule == "custom":
        pattern = Option.objects.get_value("custom_player_name_regexp") or ".*"
    else:
        pattern = r"^[A-Za-z0-9_]+$"
    try:
        if not re.match(pattern, name):
            return "角色名不符合站点规则"
    except re.error:
        return "站点角色名正则配置错误"
    return None


@login_required
@require_GET
def list_players(request):
    players = request.user.players.all().order_by("pid")
    data = [
        {
            "pid": p.pid,
            "name": p.name,
            "last_modified": p.last_modified.isoformat(),
            "tid_skin": p.tid_skin_id or 0,
            "tid_cape": p.tid_cape_id or 0,
        }
        for p in players
    ]
    return json_response("", SUCCESS, data)


def _is_ajax(request) -> bool:
    """JS fetch（bsFetch 携带 X-Requested-With）返回 JSON，浏览器直达返回页面。"""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@login_required
@require_GET
def players_page(request):
    """角色列表页与 JSON API 同 URL 协商（浏览器=页面 / AJAX=JSON）。"""
    if _is_ajax(request):
        return list_players(request)
    from django.shortcuts import render

    from options.models import Option as _Option

    return render(
        request,
        "users/players.html",
        {"site_name": _Option.objects.get_value("site_name")},
    )


@login_required
@require_POST
def add_player(request):
    name = (request.POST.get("name") or "").strip()
    if error := validate_player_name(name):
        return json_response(error, FAILURE)

    limit = _int_option("player_limit_per_user", 0)
    if limit and request.user.players.count() >= limit:
        return json_response(f"每人最多 {limit} 个角色", FAILURE)
    if Player.objects.filter(name=name).exists():
        return json_response("该角色名已被使用", FAILURE)

    player = Player(uid=request.user, name=name)
    player.uuid = generate_uuid_by_option(name)
    player.save()
    apply_default_textures(player)
    return json_response("角色创建成功", SUCCESS, {"pid": player.pid})


@login_required
def players_collection(request):
    """三协商入口：浏览器 GET=页面 / AJAX GET=JSON / POST=新增。"""
    if request.method == "GET":
        if _is_ajax(request):
            return list_players(request)
        return players_page(request)
    return add_player(request)


@login_required
@require_POST
def delete_player(request, pid: int):
    player = request.user.players.filter(pid=pid).first()
    if player is None:
        return json_response("角色不存在", FAILURE)
    player.delete()
    return json_response("角色已删除", SUCCESS)


@login_required
@require_POST
def rename_player(request, pid: int):
    player = request.user.players.filter(pid=pid).first()
    if player is None:
        return json_response("角色不存在", FAILURE)
    name = (request.POST.get("name") or "").strip()
    if error := validate_player_name(name):
        return json_response(error, FAILURE)
    if Player.objects.exclude(pid=pid).filter(name=name).exists():
        return json_response("该角色名已被使用", FAILURE)
    player.name = name
    update_fields = ["name", "last_modified"]
    # v3：UUID 为名字的函数，改名须重算（对照原版 uuid 表重映射，Profile.php:146）；
    # v4：UUID 与名字无关，保持不变（test_players_models 已锁定该语义）
    if (Option.objects.get_value("ygg_uuid_algorithm") or "v4").strip().lower() == "v3":
        player.uuid = generate_uuid_v3(name)
        update_fields.append("uuid")
    player.save(update_fields=update_fields)
    return json_response("角色已改名", SUCCESS)


@login_required
@require_POST
def set_texture(request, pid: int, kind: str):
    """设置/清除角色皮肤或披风（kind: skin/cape）。"""
    if kind not in ("skin", "cape"):
        return json_response("未知的材质类型", FAILURE)
    player = request.user.players.filter(pid=pid).first()
    if player is None:
        return json_response("角色不存在", FAILURE)

    tid = request.POST.get("tid")
    if tid in (None, "", "0"):
        setattr(player, f"tid_{kind}", None)
        player.save(update_fields=[f"tid_{kind}"])
        return json_response("已清除", SUCCESS)

    from textures.models import Texture

    try:
        texture = Texture.objects.get(tid=int(tid))
    except (ValueError, Texture.DoesNotExist):
        return json_response("纹理不存在", FAILURE)

    # 权限：公开纹理或本人上传（对照原版 can_set_texture 语义）
    if not texture.public and texture.uploader_id != request.user.uid:
        return json_response("无权使用该纹理", FAILURE)

    setattr(player, f"tid_{kind}", texture)
    player.save(update_fields=[f"tid_{kind}"])
    return json_response("已应用", SUCCESS)


@login_required
@require_POST
def set_texture_skin(request, pid: int):
    return set_texture(request, pid, "skin")


@login_required
@require_POST
def set_texture_cape(request, pid: int):
    return set_texture(request, pid, "cape")
