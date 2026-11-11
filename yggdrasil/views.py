"""Yggdrasil API 视图：认证服务器 + 会话服务器 + Profile。

对照 yggdrasil-api 插件全部端点语义（authlib-injector 规范）。
"""

import base64
import json
import secrets

from django.core.cache import cache
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from accounts.models import User
from options.models import Option
from players.models import Player

from .middleware import (
    check_body_size,
    check_content_type,
    error_response,
    get_bearer_user,
    throttle_by_ip,
)
from .models import YggdrasilLog, YggdrasilToken
from .tokens import issue_access_token, user_uuid_from_email


def _body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _json_ok(payload: dict, status: int = 200) -> HttpResponse:
    return HttpResponse(
        json.dumps(payload), status=status, content_type="application/json"
    )


def _log(request, action, user=None, player=None, params=None):
    YggdrasilLog.objects.create(
        action=action,
        user=user,
        player=player,
        parameters=json.dumps(params, ensure_ascii=False)[:512] if params else "",
        ip=request.META.get("REMOTE_ADDR", ""),
    )


# ---- 根端点（authlib-injector 发现）----


@require_GET
def hello(request):
    """站点元数据（对照原版 ConfigController@hello）。"""

    server_name = Option.objects.get_value("site_name") or "Blessing Skin"
    skin_domains = [
        d.strip()
        for d in (Option.objects.get_value("ygg_skin_domain") or "").split(",")
        if d.strip()
    ]
    if not skin_domains:

        skin_domains = [request.get_host()]

    # 公钥：优先 ygg_public_key；缺失时从 ygg_private_key 实时派生
    # （原版即如此——数据库只存私钥，公钥 openssl 实时导出）
    public_key = (Option.objects.get_value("ygg_public_key") or "").strip()
    if not public_key:
        private_pem = (Option.objects.get_value("ygg_private_key") or "").strip()
        if private_pem:
            try:
                from cryptography.hazmat.primitives import serialization

                private_key = serialization.load_pem_private_key(
                    private_pem.encode(), password=None
                )
                public_key = private_key.public_key().public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                ).decode()
            except (ValueError, TypeError):
                public_key = ""
    return _json_ok({
        "meta": {
            "serverName": server_name,
            "implementationName": "blessing-skin-django",
            "implementationVersion": "0.1.0",
            "links": {
                "homepage": f"{request.scheme}://{request.get_host()}/",
                "register": f"{request.scheme}://{request.get_host()}/auth/register",
            },
            "feature.non_email_login": True,
        },
        "skinDomains": skin_domains,
        "signaturePublickey": public_key,
    })


# ---- authenticate ----


@csrf_exempt
@require_http_methods(["POST"])
def authenticate(request):
    if body_err := check_body_size(request):
        return body_err
    if ctype_err := check_content_type(request):
        return ctype_err
    if throttle_err := throttle_by_ip(request):
        return throttle_err

    payload = _body(request)
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""

    if not username or not password:
        return error_response(
            400, "IllegalArgumentException", "账号或密码为空"
        )

    user = _find_user(username)
    if user is None or not user.verify_password(password):
        _log(request, "auth_failed", params={"username": username})
        return error_response(
            403, "ForbiddenOperationException", "账号不存在或密码错误"
        )
    if user.permission == User.Permission.BANNED:
        return error_response(403, "ForbiddenOperationException", "账号已被封禁")
    if (Option.objects.get_value("require_verification") or "0") in ("1", "true"):
        if not user.verified:
            return error_response(403, "ForbiddenOperationException", "邮箱未验证")

    # clientToken 原样返回，缺省生成
    client_token = payload.get("clientToken") or secrets.token_hex(16)

    try:
        expires_in = int(Option.objects.get_value("ygg_token_expire_1") or 259200)
        refresh_expires_in = int(
            Option.objects.get_value("ygg_token_expire_2") or 604800
        )
    except ValueError:
        expires_in, refresh_expires_in = 259200, 604800

    user_uuid = user_uuid_from_email(user.email)
    profiles = [
        {"id": p.uuid, "name": p.name} for p in user.players.all()
    ]

    token, created = YggdrasilToken.objects.get_or_create(
        owner=user,
        client_token=client_token,
        defaults={"profile_id": profiles[0]["id"] if profiles else ""},
    )
    ygg_token_uuid = secrets.token_hex(16)
    access_token = issue_access_token(user_uuid, ygg_token_uuid, expires_in)
    token.access_token = access_token
    token.expires_in = expires_in
    token.refresh_expires_in = refresh_expires_in
    if created and profiles:
        token.profile_id = profiles[0]["id"]
    token.save()

    response_payload = {
        "accessToken": access_token,
        "clientToken": client_token,
        "availableProfiles": profiles,
    }
    if payload.get("requestUser"):
        response_payload["user"] = {"id": user_uuid, "properties": []}
    if len(profiles) == 1:
        response_payload["selectedProfile"] = profiles[0]

    _log(request, "authenticate", user=user, params={"clientToken": client_token})
    return _json_ok(response_payload)


def _find_user(identification: str) -> User | None:
    if "@" in identification:
        return User.objects.filter(email=identification).first()
    player = Player.objects.filter(name=identification).select_related("uid").first()
    return player.uid if player else None


# ---- 令牌生命周期 ----


@csrf_exempt
@require_http_methods(["POST"])
def refresh(request):
    if err := check_body_size(request):
        return err
    payload = _body(request)
    access_token = payload.get("accessToken") or ""
    client_token = payload.get("clientToken")

    row = YggdrasilToken.objects.filter(access_token=access_token).first()
    if row is None or not row.is_refreshable():
        return error_response(403, "ForbiddenOperationException", "令牌无效或已过期")

    user = row.owner
    if user.permission == User.Permission.BANNED:
        return error_response(403, "ForbiddenOperationException", "账号已被封禁")

    if client_token and row.client_token != client_token:
        return error_response(403, "ForbiddenOperationException", "clientToken 不匹配")

    user_uuid = user_uuid_from_email(user.email)
    profiles = [{"id": p.uuid, "name": p.name} for p in user.players.all()]

    selected = payload.get("selectedProfile")
    if selected and isinstance(selected, dict):
        target = next((p for p in profiles if p["id"] == selected.get("id")), None)
        if target is None:
            return error_response(
                403, "ForbiddenOperationException", "角色不属于该用户"
            )
        row.profile_id = target["id"]

    # 轮换：新 accessToken（旧 token 随覆盖即刻失效）
    new_access = issue_access_token(user_uuid, secrets.token_hex(16), row.expires_in)
    row.access_token = new_access
    row.save(update_fields=["access_token"])

    response_payload = {
        "accessToken": new_access,
        "clientToken": row.client_token,
        "availableProfiles": profiles,
    }
    if payload.get("requestUser"):
        response_payload["user"] = {"id": user_uuid, "properties": []}
    selected_profile = next(
        (p for p in profiles if p["id"] == row.profile_id), None
    )
    if selected_profile:
        response_payload["selectedProfile"] = selected_profile
    _log(request, "refresh", user=user)
    return _json_ok(response_payload)


@csrf_exempt
@require_http_methods(["POST"])
def validate(request):
    payload = _body(request)
    access_token = payload.get("accessToken") or ""
    client_token = payload.get("clientToken")

    row = YggdrasilToken.objects.filter(access_token=access_token).first()
    if row is None or not row.is_valid():
        return error_response(403, "ForbiddenOperationException", "令牌无效")
    if client_token and row.client_token != client_token:
        return error_response(403, "ForbiddenOperationException", "clientToken 不匹配")
    if row.owner.permission == User.Permission.BANNED:
        return error_response(403, "ForbiddenOperationException", "账号已被封禁")
    return HttpResponse(status=204)


@csrf_exempt
@require_http_methods(["POST"])
def invalidate(request):
    payload = _body(request)
    access_token = payload.get("accessToken") or ""
    deleted, _ = YggdrasilToken.objects.filter(access_token=access_token).delete()
    # 原版语义：无论是否成功均 204
    return HttpResponse(status=204)


@csrf_exempt
@require_http_methods(["POST"])
def signout(request):
    if throttle_err := throttle_by_ip(request):
        return throttle_err
    payload = _body(request)
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""

    user = _find_user(username)
    if user is None or not user.verify_password(password):
        return error_response(403, "ForbiddenOperationException", "账号或密码错误")
    YggdrasilToken.objects.filter(owner=user).delete()
    _log(request, "signout", user=user)
    return HttpResponse(status=204)


# ---- 会话服务器 ----


@csrf_exempt
@require_http_methods(["POST"])
def join(request):
    if err := check_body_size(request):
        return err
    payload = _body(request)
    access_token = payload.get("accessToken") or ""
    selected_profile = payload.get("selectedProfile") or ""
    server_id = payload.get("serverId") or ""

    row = YggdrasilToken.objects.filter(access_token=access_token).first()
    if row is None or not row.is_valid():
        return error_response(403, "ForbiddenOperationException", "令牌无效")

    player = Player.objects.filter(uuid=selected_profile).select_related("uid").first()
    if player is None or player.uid_id != row.owner_id:
        return error_response(403, "ForbiddenOperationException", "角色与令牌不匹配")
    if row.profile_id and row.profile_id != selected_profile:
        return error_response(403, "ForbiddenOperationException", "令牌未选中该角色")
    if player.uid.permission == User.Permission.BANNED:
        return error_response(403, "ForbiddenOperationException", "账号已被封禁")

    # 会话登记（cache，120s，对照原版）
    session_key = f"ygg_server:{server_id}:{player.name}"
    cache.set(session_key, player.uuid, 120)

    _log(request, "join", user=player.uid, player=player, params={"serverId": server_id})
    return HttpResponse(status=204)


@require_GET
def has_joined(request):
    username = request.GET.get("username") or ""
    server_id = request.GET.get("serverId") or ""

    session_key = f"ygg_server:{server_id}:{username}"
    player_uuid = cache.get(session_key)
    if not player_uuid:
        return HttpResponse(status=204)

    player = Player.objects.filter(uuid=player_uuid).select_related(
        "uid", "tid_skin", "tid_cape"
    ).first()
    if player is None or player.name != username:
        return HttpResponse(status=204)

    profile = _profile_payload(request, player, signed=False)
    _log(request, "has_joined", user=player.uid, player=player,
         params={"serverId": server_id})
    return _json_ok(profile)


@require_GET
def get_profile(request, uuid: str):
    player = Player.objects.filter(uuid=uuid).select_related(
        "uid", "tid_skin", "tid_cape"
    ).first()
    if player is None:
        return HttpResponse(status=204)
    unsigned = (request.GET.get("unsigned") or "true").lower() != "false"
    return _json_ok(_profile_payload(request, player, signed=not unsigned))


def _profile_payload(request, player: Player, signed: bool) -> dict:
    """Profile 序列化（对照原版 Profile::serialize）。

    textures property：base64(JSON{timestamp, profileId, profileName, textures})
    signed 时附 RSA-SHA256 签名（ygg_private_key option）。
    """
    import time as _time

    textures = {
        "timestamp": int(_time.time() * 1000),
        "profileId": player.uuid,
        "profileName": player.name,
        "isPublic": True,
        "textures": {},
    }
    base_url = f"{request.scheme}://{request.get_host()}"
    if player.tid_skin:
        entry = {"url": f"{base_url}/textures/{player.tid_skin.hash}"}
        if player.tid_skin.type == "alex":
            entry["metadata"] = {"model": "slim"}
        textures["textures"]["SKIN"] = entry
    if player.tid_cape:
        textures["textures"]["CAPE"] = {
            "url": f"{base_url}/textures/{player.tid_cape.hash}"
        }

    properties = [
        {
            "name": "textures",
            "value": base64.b64encode(
                json.dumps(textures, separators=(",", ":")).encode()
            ).decode(),
        }
    ]
    if signed:
        signature = _sign_textures(properties[0]["value"])
        if signature:
            properties[0]["signature"] = signature

    return {
        "id": player.uuid,
        "name": player.name,
        "properties": properties,
    }


def _sign_textures(value_b64: str) -> str | None:
    """RSA-SHA256 签名（对照原版 openssl_sign）。"""
    pem = (Option.objects.get_value("ygg_private_key") or "").strip()
    if not pem:
        return None

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        signature = key.sign(
            value_b64.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        return base64.b64encode(signature).decode()
    except (ValueError, TypeError):
        return None


# ---- Profile 批量/单个查询 ----


@csrf_exempt
@require_http_methods(["POST"])
def search_profiles(request):
    names = _body(request)
    if not isinstance(names, list):
        return error_response(400, "IllegalArgumentException", "请求体须为角色名数组")
    try:
        max_names = int(Option.objects.get_value("ygg_search_profile_max") or 5)
    except ValueError:
        max_names = 5
    if len(names) > max_names:
        return error_response(
            403,
            "ForbiddenOperationException",
            f"单次查询不能超过 {max_names} 个角色",
        )
    found = [
        {"id": p.uuid, "name": p.name}
        for p in Player.objects.filter(name__in=names)
    ]
    return _json_ok(found)


@require_GET
def search_single_profile(request, username: str):
    player = Player.objects.filter(name=username).first()
    if player is None:
        return HttpResponse(status=204)
    return _json_ok({"id": player.uuid, "name": player.name})


# ---- 材质上传 / 重置（Bearer）----


@csrf_exempt
@require_http_methods(["PUT", "DELETE"])
def upload_texture(request, uuid: str, type_: str):
    user, row = get_bearer_user(request)
    if user is None:
        return error_response(403, "ForbiddenOperationException", "无效的访问令牌")

    player = Player.objects.filter(uuid=uuid, uid=user).first()
    if player is None:
        return error_response(
            404, "NotFoundException", "角色不存在或不属于当前用户"
        )
    if type_ not in ("skin", "cape"):
        return error_response(400, "IllegalArgumentException", "未知的材质类型")

    if request.method == "DELETE":
        setattr(player, f"tid_{type_}", None)
        player.save(update_fields=[f"tid_{type_}"])
        _log(request, f"reset_{type_}", user=user, player=player)
        return HttpResponse(status=204)

    if err := check_body_size(request):
        return err

    from textures.storage import texture_storage
    from textures.validation import validate_texture_image

    data = request.body
    texture_type = type_ if type_ == "cape" else "steve"
    # slim/steve 模型由查询参数 model 控制（对照原版 multipart model 字段）
    model = request.GET.get("model") or request.headers.get("X-Texture-Model") or ""
    if type_ == "skin" and model == "slim":
        texture_type = "alex"

    result = validate_texture_image(data, texture_type)
    if not result.valid:
        return error_response(400, "IllegalArgumentException", result.error)

    from textures.models import Texture

    texture = Texture.objects.filter(hash=result.digest).first()
    if texture is None:
        import math

        size_kb = math.ceil(len(result.normalized) / 1024)
        texture = Texture.objects.create(
            name=player.name,
            type=texture_type,
            hash=result.digest,
            size=size_kb,
            uploader=user,
            public=False,
        )
        texture_storage.save_bytes(result.digest, result.normalized)

    setattr(player, f"tid_{type_}", texture)
    player.save(update_fields=[f"tid_{type_}"])
    _log(request, f"upload_{type_}", user=user, player=player,
         params={"hash": result.digest})
    return HttpResponse(status=204)
