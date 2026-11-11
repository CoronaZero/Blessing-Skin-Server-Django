"""textures 视图：皮肤库搜索/上传/纹理文件/头像/CustomSkinAPI。"""

import math

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseNotFound, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from config.jsonapi import SPECIAL, SUCCESS, json_response
from options.models import Option
from players.models import Player

from .models import Texture
from .rendering import AVATAR_SIZES, render_avatar, render_texture_response
from .storage import TextureFileNotFoundError, texture_storage
from .validation import validate_texture_image

# ---- 页面 ----


@login_required
def skinlib_page(request):
    """皮肤库页面（登录后可见，对照原版）。"""
    from django.shortcuts import render

    return render(
        request,
        "textures/skinlib.html",
        {
            "keyword": request.GET.get("keyword", ""),
            "category": request.GET.get("category", "skin"),
            "sort": request.GET.get("sort", "time"),
            "site_name": Option.objects.get_value("site_name"),
        },
    )


# ---- 皮肤库搜索（JSON 分页，对照原版 SkinlibController::search）----


@login_required
@require_GET
def search(request):
    from django.core.paginator import Paginator

    keyword = (request.GET.get("keyword") or "").strip()
    filter_type = (request.GET.get("filter") or "").strip()
    category = (request.GET.get("category") or "skin").strip()
    sort = (request.GET.get("sort") or "time").strip()
    try:
        page = int(request.GET.get("page") or 1)
    except ValueError:
        page = 1

    queryset = Texture.objects.filter(public=True).select_related("uploader")
    if category == "cape":
        queryset = queryset.filter(type="cape")
    elif category == "alex":
        queryset = queryset.filter(type="alex")
    elif category in ("skin", "steve"):
        queryset = queryset.filter(type="steve")

    if keyword:
        queryset = queryset.filter(name__icontains=keyword)
    if filter_type:
        queryset = queryset.filter(type=filter_type)

    sort_columns = {
        "time": "-tid",
        "size": "-size",
        "likes": "-likes",
    }
    queryset = queryset.order_by(sort_columns.get(sort, "-tid"))

    paginator = Paginator(queryset, 20)
    page_obj = paginator.get_page(page)
    items = [
        {
            "tid": t.tid,
            "name": t.name,
            "type": t.type,
            "likes": t.likes,
            "hash": t.hash,
            "size": t.size,
            "uploader": t.uploader.nickname,
            "public": t.public,
        }
        for t in page_obj.object_list
    ]
    return json_response(
        "",
        SUCCESS,
        {
            "data": items,
            "current_page": page_obj.number,
            "last_page": paginator.num_pages,
            "total": paginator.count,
        },
    )


# ---- 上传（对照原版 SkinlibController::upload 计分语义）----


@login_required
@require_POST
def upload(request):
    name = (request.POST.get("name") or "").strip()
    texture_type = (request.POST.get("type") or "").strip()
    is_public = request.POST.get("public") in ("1", "true", "on")
    uploaded = request.FILES.get("file")

    if not uploaded:
        return json_response("请选择要上传的文件")
    if not name:
        name = uploaded.name.rsplit(".", 1)[0] if uploaded.name else "未命名"
    if texture_type not in ("steve", "alex", "cape"):
        return json_response("未知的纹理类型")

    result = validate_texture_image(uploaded.read(), texture_type)
    if not result.valid:
        return json_response(result.error)

    user = request.user

    # 去重：已存在（public 或本人上传）→ code=2 + tid
    duplicated = (
        Texture.objects.filter(hash=result.digest, public=True).first()
        or Texture.objects.filter(hash=result.digest, uploader=user).first()
    )
    if duplicated:
        return json_response("重复的纹理", SPECIAL, {"tid": duplicated.tid})

    # 计分（对照原版 score_per_storage / private_score_per_storage）
    size_kb = math.ceil(len(result.normalized) / 1024)
    try:
        per_storage = int(
            Option.objects.get_value("score_per_storage")
            or Option.objects.get_value("private_score_per_storage")
            or 0
        )
    except ValueError:
        per_storage = 0
    try:
        per_closet = int(Option.objects.get_value("score_per_closet_item") or 0)
    except ValueError:
        per_closet = 0
    try:
        award = int(Option.objects.get_value("score_award_per_texture") or 0)
    except ValueError:
        award = 0

    cost = size_kb * per_storage + per_closet - award
    if cost > user.score:
        return json_response("积分不足，无法上传")

    texture = Texture.objects.create(
        name=name,
        type=texture_type,
        hash=result.digest,
        size=size_kb,
        uploader=user,
        public=is_public,
    )
    if not texture_storage.exists(result.digest):
        texture_storage.save_bytes(result.digest, result.normalized)
    if cost != 0:
        user.score -= cost
        user.save(update_fields=["score"])

    return json_response("上传成功", SUCCESS, {"tid": texture.tid})


# ---- 纹理文件 / 头像 / CustomSkinAPI R1 ----


@require_GET
def serve_texture(request, hash_value: str):
    try:
        data, headers = render_texture_response(hash_value)
    except (TextureFileNotFoundError, ValueError):
        return HttpResponseNotFound()

    if_none_match = request.META.get("HTTP_IF_NONE_MATCH", "")
    if if_none_match == headers["ETag"]:
        response = HttpResponse(status=304)
    else:
        response = HttpResponse(data, content_type=headers["Content-Type"])
    for key, value in headers.items():
        response[key] = value
    return response


@require_GET
def serve_avatar(request, size: int, player_name: str):
    if size not in AVATAR_SIZES:
        return HttpResponseNotFound()
    try:
        player = Player.objects.select_related("tid_skin", "uid").get(
            name=player_name.removesuffix(".png")
        )
    except Player.DoesNotExist:
        return HttpResponse(status=404)

    if not player.tid_skin:
        return HttpResponse(status=404)
    try:
        skin = texture_storage.read(player.tid_skin.hash)
    except (TextureFileNotFoundError, ValueError):
        return HttpResponse(status=404)

    avatar = render_avatar(skin, size)
    return HttpResponse(avatar, content_type="image/png")


@require_GET
def player_profile_json(request, player_name: str):
    """CustomSkinAPI R1：/{player}.json。"""
    player_name = player_name.removesuffix(".json")
    try:
        player = Player.objects.select_related("tid_skin", "tid_cape").get(
            name=player_name
        )
    except Player.DoesNotExist:
        return HttpResponseNotFound()

    return JsonResponse(player.to_profile_dict())


# ---- 用户衣柜（对照原版 ClosetController）----


@login_required
@require_GET
def closet_list(request):
    items = request.user.closet.select_related("texture").order_by("-added_at")
    data = [
        {
            "id": item.id,
            "tid": item.texture.tid,
            "name": item.item_name or item.texture.name,
            "type": item.texture.type,
            "hash": item.texture.hash,
            "added_at": item.added_at.isoformat(),
        }
        for item in items
    ]
    return json_response("", SUCCESS, data)


@login_required
@require_POST
def closet_add(request):
    tid = request.POST.get("tid", "")
    name = (request.POST.get("name") or "").strip()
    try:
        texture = Texture.objects.get(tid=int(tid))
    except (ValueError, Texture.DoesNotExist):
        return json_response("纹理不存在")

    # 权限：公开或本人（对照原版）
    if not texture.public and texture.uploader_id != request.user.uid:
        return json_response("无权收藏该纹理")

    if request.user.closet.filter(texture=texture).exists():
        return json_response("该纹理已在衣柜中")

    from options.models import Option as _Option

    try:
        cost = int(_Option.objects.get_value("score_per_closet_item") or 0)
    except ValueError:
        cost = 0
    if cost > request.user.score:
        return json_response("积分不足")
    request.user.score -= cost
    request.user.save(update_fields=["score"])

    from .models import ClosetItem

    ClosetItem.objects.create(owner=request.user, texture=texture, item_name=name)
    return json_response("已加入衣柜", SUCCESS)


@login_required
def closet_collection(request):
    """GET 列表 / POST 添加（同一 URL）。"""
    if request.method == "GET":
        return closet_list(request)
    return closet_add(request)


@login_required
@require_POST
def closet_rename(request, item_id: int):
    item = request.user.closet.filter(id=item_id).first()
    if item is None:
        return json_response("衣柜项不存在")
    item.item_name = (request.POST.get("name") or "").strip()[:255]
    item.save(update_fields=["item_name"])
    return json_response("已重命名", SUCCESS)


@login_required
@require_POST
def closet_remove(request, item_id: int):
    item = request.user.closet.filter(id=item_id).first()
    if item is None:
        return json_response("衣柜项不存在")
    item.delete()
    return json_response("已移出衣柜", SUCCESS)


# ---- 举报（对照原版 ReportController@submit）----


@login_required
@require_POST
def report_submit(request):
    tid = request.POST.get("tid", "")
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        return json_response("请填写举报理由")
    try:
        texture = Texture.objects.get(tid=int(tid))
    except (ValueError, Texture.DoesNotExist):
        return json_response("纹理不存在")

    from moderation.models import Report

    Report.objects.create(
        texture=texture,
        uploader=texture.uploader,
        reporter=request.user,
        reason=reason,
    )
    return json_response("举报已提交，感谢反馈", SUCCESS)
