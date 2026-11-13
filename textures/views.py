"""textures 视图：皮肤库搜索/上传/纹理文件/头像/CustomSkinAPI。"""

import io
import math

from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.http import HttpResponse, HttpResponseNotFound, JsonResponse
from django.views.decorators.http import require_GET, require_POST
from PIL import Image

from accounts import restrictions
from accounts.models import User
from config.jsonapi import FAILURE, SPECIAL, SUCCESS, json_response
from options.models import Option
from players.models import Player

from .models import Texture
from .rendering import AVATAR_SIZES, render_avatar, render_texture_response
from .skinrender import render_cape_portrait, render_skin_portrait
from .storage import TextureFileNotFoundError, texture_storage
from .validation import validate_texture_image

# ---- 页面 ----


def _default_skin_texture():
    """管理员配置的默认皮肤（default_skin_tid；无效/未配置返回 None）。

    披风 3D 预览需要一张皮肤作"人台"挂载披风。
    """
    raw = (Option.objects.get_value("default_skin_tid") or "0").strip()
    try:
        tid = int(raw)
    except ValueError:
        return None
    if tid <= 0:
        return None
    return Texture.objects.filter(tid=tid).first()


@login_required
def upload_page(request):
    """上传页（GET 渲染表单；POST 由同 URL 的 upload API 处理）。"""
    if restrictions.blocked(request, "unverified_block_upload"):
        return restrictions.restricted_page(
            request, "未验证邮箱的账号暂时不能上传皮肤。"
        )
    import json as _json

    from django.shortcuts import render

    per_storage = Option.objects.get_value("score_per_storage") or "0"
    score_hint = f"上传将按体积计分（每 KB {per_storage} 分）。" if per_storage != "0" else ""
    # 披风实时预览的"人台"：默认皮肤（未配置时 JS 回落平铺图）
    default_skin = _default_skin_texture()
    default_skin_json = ""
    if default_skin:
        default_skin_json = _json.dumps({
            "url": f"/textures/{default_skin.hash}",
            "model": "slim" if default_skin.type == "alex" else "default",
        })
    # 站外导入开关（默认关闭）与倍率提示
    import_enabled = (Option.objects.get_value("import_enabled") or "0").strip().lower() in (
        "1", "true", "on",
    )
    try:
        import_multiplier = int(
            Option.objects.get_value("import_score_multiplier") or 3
        )
    except ValueError:
        import_multiplier = 3
    return render(
        request,
        "textures/upload.html",
        {
            "site_name": Option.objects.get_value("site_name"),
            "score_hint": score_hint,
            "default_skin_json": default_skin_json,
            "import_enabled": import_enabled,
            "import_multiplier": import_multiplier,
        },
    )


@login_required
def skinlib_page(request):
    """皮肤库页面（登录后可见，对照原版）。"""
    if restrictions.blocked(request, "unverified_block_skinlib"):
        return restrictions.restricted_page(
            request, "未验证邮箱的账号暂时不能浏览皮肤库。"
        )
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


@login_required
def texture_show_page(request):
    """纹理详情页（3D 预览 + 收藏/下载/举报/管理）。"""
    if restrictions.blocked(request, "unverified_block_skinlib"):
        return restrictions.restricted_page(
            request, "未验证邮箱的账号暂时不能浏览皮肤库。"
        )
    from django.shortcuts import get_object_or_404, render

    texture = get_object_or_404(
        Texture.objects.select_related("uploader"), tid=request.GET.get("tid", 0)
    )
    # 私密纹理仅上传者与管理员可见（越权审计：普通用户改 tid 访问他人私密 → 404，已有测试锁定）
    can_manage = (
        texture.uploader_id == request.user.uid or request.user.is_admin()
    )
    if not texture.public and not can_manage:
        from django.http import HttpResponseNotFound

        return HttpResponseNotFound()

    # 本人衣柜项 id（按 owner 过滤，天然权限隔离）：已收藏 → 渲染"移出"态
    closet_item_id = request.user.closet.filter(
        texture=texture
    ).values_list("id", flat=True).first()

    # 披风 3D 预览的"人台"：管理员配置的默认皮肤
    default_skin = _default_skin_texture() if texture.type == "cape" else None

    return render(
        request,
        "textures/show.html",
        {
            "texture": texture,
            "site_name": Option.objects.get_value("site_name"),
            "can_manage": can_manage,
            "closet_item_id": closet_item_id or "",
            "default_skin": default_skin,
        },
    )


# ---- 材质管理（上传者/管理员，对照原版 SkinlibController rename/privacy/delete）----


def _manage_guard(request, tid: int):
    """取纹理并校验管理权限；返回 (texture, error_response)。

    管理权 = 上传者本人或管理员（与原版 can_* Filter 默认语义、
    本项目面板权限一致）。
    """
    texture = Texture.objects.select_related("uploader").filter(tid=tid).first()
    if texture is None:
        return None, json_response("纹理不存在")
    if texture.uploader_id != request.user.uid and not request.user.is_admin():
        return None, json_response("无权操作该纹理")
    return texture, None


def _opt_int(key: str, fallback: int = 0) -> int:
    try:
        return int(Option.objects.get_value(key) or fallback)
    except ValueError:
        return fallback


@login_required
@require_POST
def texture_privacy(request, tid: int):
    """切换公开/私密（对照原版 privacy，含积分差价）。

    差价公式（原版 SkinlibController.php:365-374）：
      diff = size * (private_score_per_storage - score_per_storage)
             * (当前公开 ? -1 : +1)   # 加到上传者积分上的增量
    转私（原状态公开）另收回上传奖励（原版 take_back_scores_after_deletion
    默认 true，未暴露选项，按默认行为执行）；积分不足（score+diff<0）→ 拒绝。
    转公开前若已存在同 hash 公开纹理 → code=2 + tid（与上传重复语义一致）。
    """
    texture, err = _manage_guard(request, tid)
    if err:
        return err

    uploader = texture.uploader
    if texture.public:
        diff = (
            -texture.size
            * (_opt_int("private_score_per_storage") - _opt_int("score_per_storage"))
            - _opt_int("score_award_per_texture")
        )
        # 条件 UPDATE 原子完成余额校验与扣减（读-改-写并发可透支为负）
        qs = User.objects.filter(uid=uploader.uid)
        if diff < 0:
            qs = qs.filter(score__gte=-diff)
        if not qs.update(score=F("score") + diff):
            return json_response("积分不足，无法设为私密")
        texture.public = False
        texture.save(update_fields=["public"])
        return json_response("已设为私密", SUCCESS)

    # 私密 → 公开：查同 hash 公开纹理（原版 L376-383；本模型 hash 全局
    # 唯一、正常不可触发，保留为防御分支——约束变化时自动生效）
    duplicated = (
        Texture.objects.filter(hash=texture.hash, public=True)
        .exclude(tid=texture.tid)
        .first()
    )
    if duplicated:
        return json_response("重复的纹理", SPECIAL, {"tid": duplicated.tid})

    # 转公开返还差价（原版 take_back 语义：公开材质不收私密存储费）
    refund = texture.size * (
        _opt_int("private_score_per_storage") - _opt_int("score_per_storage")
    )
    qs = User.objects.filter(uid=uploader.uid)
    if refund < 0:  # 误配时扣减仍不可透支
        qs = qs.filter(score__gte=-refund)
    qs.update(score=F("score") + refund)
    texture.public = True
    texture.save(update_fields=["public"])
    return json_response("已设为公开", SUCCESS)


@login_required
@require_POST
def texture_rename(request, tid: int):
    """重命名（对照原版 rename；名称长度对齐模型列 50）。"""
    texture, err = _manage_guard(request, tid)
    if err:
        return err
    name = (request.POST.get("name") or "").strip()[:50]
    if not name:
        return json_response("名称不能为空")
    texture.name = name
    texture.save(update_fields=["name"])
    return json_response("已重命名", SUCCESS)


@login_required
@require_POST
def texture_remove(request, tid: int):
    """删除材质（上传者/管理员；清理佩戴/衣柜/举报引用）。"""
    texture, err = _manage_guard(request, tid)
    if err:
        return err
    texture.delete_with_cleanup()
    return json_response("已删除", SUCCESS)


# ---- 皮肤库搜索（JSON 分页，对照原版 SkinlibController::search）----


@login_required
@require_GET
def search(request):
    if restrictions.blocked(request, "unverified_block_skinlib"):
        return json_response("请先验证邮箱后再浏览皮肤库", FAILURE, status=403)
    from django.core.paginator import Paginator

    keyword = (request.GET.get("keyword") or "").strip()
    filter_type = (request.GET.get("filter") or "").strip()
    category = (request.GET.get("category") or "skin").strip()
    sort = (request.GET.get("sort") or "time").strip()
    try:
        page = int(request.GET.get("page") or 1)
    except ValueError:
        page = 1

    # 可见性对照原版 library：admin 全可见；普通用户=公开+自己的
    if request.user.is_admin():
        queryset = Texture.objects.select_related("uploader")
    else:
        from django.db.models import Q

        queryset = Texture.objects.filter(
            Q(public=True) | Q(uploader=request.user)
        ).select_related("uploader")
    if category == "cape":
        queryset = queryset.filter(type="cape")
    elif category == "alex":
        queryset = queryset.filter(type="alex")
    elif category in ("skin", "steve"):
        # 对照原版：skin 分类包含 steve+alex（此前漏掉 alex 导致列表缺皮肤）
        queryset = queryset.filter(type__in=["steve", "alex"])

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
    if restrictions.blocked(request, "unverified_block_upload"):
        return json_response("请先验证邮箱后再上传皮肤", FAILURE, status=403)
    name = (request.POST.get("name") or "").strip()
    texture_type = (request.POST.get("type") or "").strip()
    is_public = request.POST.get("public") in ("1", "true", "on")
    uploaded = request.FILES.get("file")

    if not uploaded:
        return json_response("请选择要上传的文件")
    # 体积上限接选项 max_upload_file_size（KB，对照 PHP 原版执行项；
    # Yggdrasil API 路径维持其 4MiB 独立上限，不受此选项影响）
    max_kb = _opt_int("max_upload_file_size", 2048)
    if uploaded.size > max_kb * 1024:
        return json_response(f"文件体积超过上限（{max_kb}KB）")
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
    # 条件 UPDATE 原子完成余额校验与扣减（读-改-写并发可透支为负）；
    # 先扣费后建档，避免建档成功而扣费竞态失败
    if cost:
        if not User.objects.filter(uid=user.uid, score__gte=cost).update(
            score=F("score") - cost
        ):
            return json_response("积分不足，无法上传")
        user.refresh_from_db()

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
        avatar = render_avatar(skin, size)
    except (TextureFileNotFoundError, ValueError):
        # RenderTooLargeError 为 ValueError 子类：超大像素图一律 404，防内存放大
        return HttpResponse(status=404)

    return HttpResponse(avatar, content_type="image/png")


@login_required
@require_GET
def serve_my_avatar(request, size: int):
    """当前用户头像：avatar 字段（用户自选纹理）→ 回落首个角色皮肤。

    URL 形如 /avatar/me/64.png（区别于按玩家名的 CustomSkinAPI 路径）。
    无可用皮肤时 302 到占位图标（前端 <img> 兜底显示）。
    """
    from django.shortcuts import redirect as _redirect

    if size not in AVATAR_SIZES:
        return HttpResponseNotFound()

    skin_hash = ""
    if request.user.avatar:
        t = Texture.objects.filter(tid=request.user.avatar).first()
        if t and t.type in ("steve", "alex"):
            skin_hash = t.hash
    if not skin_hash:
        player = request.user.players.select_related("tid_skin").first()
        if player and player.tid_skin:
            skin_hash = player.tid_skin.hash
    if not skin_hash:
        # 无皮肤：占位图（静态资源而非 404，避免浏览器显示裂图）
        return _redirect("/static/icons/user.svg")

    try:
        skin = texture_storage.read(skin_hash)
        avatar = render_avatar(skin, size)
    except (TextureFileNotFoundError, ValueError):
        return _redirect("/static/icons/user.svg")
    # 个人选择可能变化，短缓存即可
    resp = HttpResponse(avatar, content_type="image/png")
    resp["Cache-Control"] = "private, max-age=300"
    return resp


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


# ---- 皮肤立绘预览（对照原版 /preview/{tid}，3D 投影渲染 + 缓存）----


@require_GET
def preview_texture(request, tid: int):
    """渲染纹理立绘：skin→正面+背面 3D 立绘；cape→披风平展图。"""
    from django.core.cache import cache

    texture = Texture.objects.filter(tid=tid).first()
    if texture is None:
        return HttpResponseNotFound()

    use_png = "png" in request.GET
    height = request.GET.get("height", "200")
    cache_key = f"preview:t{texture.tid}:{'png' if use_png else 'webp'}"
    cached = cache.get(cache_key)
    if cached is None:
        try:
            data = texture_storage.read(texture.hash)
            if texture.type == "cape":
                image_bytes = render_cape_portrait(data, int(height))
                fmt = "PNG"
            else:
                image_bytes = render_skin_portrait(data, 12, texture.type == "alex")
                fmt = "PNG"
        except (TextureFileNotFoundError, ValueError):
            # 渲染超像素上限/文件缺失/height 非法均为 404，不 500
            return HttpResponse(status=404)
        if not use_png:
            try:
                img = Image.open(io.BytesIO(image_bytes))
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=100)
                image_bytes, fmt = buf.getvalue(), "WEBP"
            except Exception:  # noqa: BLE001 - 无 webp 编码器时回落 PNG
                pass
        cached = (fmt, image_bytes)
        from options.models import Option

        try:
            persist = (Option.objects.get_value("enable_preview_cache") or "0") in ("1", "true")
        except Exception:  # noqa: BLE001 - options 表未就绪时短缓存
            persist = False
        cache.set(cache_key, cached, 3600 * 24 * 365 if persist else 60)

    fmt, image_bytes = cached
    resp = HttpResponse(image_bytes, content_type=f"image/{fmt.lower()}")
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


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
    # 条件 UPDATE 原子完成余额校验与扣减（读-改-写并发可透支为负）
    if cost:
        if not User.objects.filter(
            uid=request.user.uid, score__gte=cost
        ).update(score=F("score") - cost):
            return json_response("积分不足")
        request.user.refresh_from_db()

    from .models import ClosetItem

    item = ClosetItem.objects.create(owner=request.user, texture=texture, item_name=name)
    return json_response("已加入衣柜", SUCCESS, {"item_id": item.id})


@login_required
@require_GET
def closet_page(request):
    """衣柜独立页（分页改造）。"""
    from django.shortcuts import render

    return render(
        request,
        "users/closet.html",
        {"site_name": Option.objects.get_value("site_name")},
    )


def _is_ajax(request) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@login_required
def closet_collection(request):
    """三协商：浏览器 GET=页面 / AJAX GET=JSON / POST=添加。"""
    if request.method == "GET":
        if _is_ajax(request):
            return closet_list(request)
        return closet_page(request)
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
