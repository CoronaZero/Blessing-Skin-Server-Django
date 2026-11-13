"""Yggdrasil API 视图：认证服务器 + 会话服务器 + Profile。

对照 yggdrasil-api 插件全部端点语义（authlib-injector 规范）。
"""

import base64
import json
import secrets

from django.conf import settings
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
    client_ip,
    error_response,
    get_bearer_user,
    throttle_by_ip,
)
from .models import YggdrasilLog, YggdrasilToken
from .tokens import issue_access_token, user_uuid_from_email

# multipart 上传的文件本体上限（PUT multipart 不走 1MiB body 限制，
# 高清材质另设 4MiB；裸 body 上传仍按 YGG_MAX_BODY_BYTES 默认 1MiB）
UPLOAD_MAX_FILE_BYTES = 4 * 1024 * 1024


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
    """接口审计日志：对照原版 ygg_log()，仅 YGG_VERBOSE_LOG 开启时落库。

    IP 取反代后的真实客户端地址（XFF 首段，与 join/hasJoined 审计口径一致）。
    """
    if not getattr(settings, "YGG_VERBOSE_LOG", False):
        return
    YggdrasilLog.objects.create(
        action=action,
        user=user,
        player=player,
        parameters=json.dumps(params, ensure_ascii=False)[:512] if params else "",
        ip=client_ip(request),
    )


# ---- 根端点（authlib-injector 发现）----


def _normalize_domain(entry: str) -> str:
    """域名归一：小写 + 剥离端口（客户端按 URI.getHost() 匹配，规范 L762-769）。"""
    from urllib.parse import urlparse

    entry = (entry or "").strip().lower()
    if not entry:
        return ""
    return (urlparse("//" + entry).hostname or entry).lower()


def _public_base_url(request) -> str:
    """对外可访问的站点根地址（原版 forceRootUrl(option('site_url'))，
    Profile.php:59）：hasJoined 由 Minecraft 服务端发起，经反代后
    request.get_host() 可能是内网地址，故 site_url 优先；未配置时
    回落当前请求（邮件场景的 localhost 容错不适用于材质 URL）。"""
    configured = (Option.objects.get_value("site_url") or "").strip().rstrip("/")
    if configured:
        if not configured.startswith(("http://", "https://")):
            configured = "https://" + configured
        return configured
    return f"{request.scheme}://{request.get_host()}"


@require_GET
def hello(request):
    """站点元数据（对照原版 ConfigController@hello）。"""

    server_name = Option.objects.get_value("site_name") or "Blessing Skin"
    # 材质域名白名单（规范 L762-769）：配置域名 + 当前 host + site_url host 合并。
    # 客户端按 URI.getHost() 匹配（小写、无端口、大小写敏感），故统一归一。
    skin_domains: list[str] = []

    def add_domain(entry: str) -> None:
        norm = _normalize_domain(entry)
        if norm and norm not in skin_domains:
            skin_domains.append(norm)

    for d in (Option.objects.get_value("ygg_skin_domain") or "").split(","):
        add_domain(d)
    add_domain(request.get_host())
    site_url = (Option.objects.get_value("site_url") or "").strip()
    if site_url:
        from urllib.parse import urlparse

        add_domain(urlparse(site_url).hostname or "")

    # 公钥：惰性确保密钥存在后派生（原版数据库只存私钥，公钥 openssl 实时导出）
    _ensure_signing_key()
    public_key = _public_key_pem()
    # 关闭注册时移除 register 链接（原版 ConfigController.php:96-98）
    base = _public_base_url(request)
    links = {"homepage": base + "/"}
    if (Option.objects.get_value("disable_registration") or "0") not in ("1", "true", "on"):
        links["register"] = base + "/auth/register"
    meta = {
        "serverName": server_name,
        "implementationName": "blessing-skin-django",
        "implementationVersion": "0.1.0",
        "links": links,
        "feature.non_email_login": True,
    }
    # 好友系统总开关开启时声明 enable_mojang_anti_features：让 authlib-injector
    # 不再本地拦截 /player/attributes 等端点、转而反代到本站（规范 §2 硬前提）。
    # 关闭时保持注入器默认（好友 UI 不可用），端点实现不产生副作用。
    if (Option.objects.get_value("friends_enabled") or "0").strip().lower() in (
        "1", "true", "on",
    ):
        meta["feature.enable_mojang_anti_features"] = True
    resp = _json_ok({
        "meta": meta,
        "skinDomains": skin_domains,
        "signaturePublickey": public_key,
    })
    # ALI 头由全站中间件统一附加（ApiIndicationMiddleware，对照原版
    # AddApiIndicationHeader）；此处不再单独设置，避免双来源分叉。
    return resp


def _ensure_signing_key() -> None:
    """确保签名私钥存在：缺失时生成 4096 位 RSA 密钥对。

    P0（authlib 客户端兼容性）：客户端 APIMetadata 解析要求
    signaturePublickey 为合法 PEM——空串会令 KeyUtils 抛异常、启动器
    直接退出。PHP 原版在启用 yggdrasil-api 插件时生成密钥，从旧站
    迁移（未启用过插件）则缺密钥，须在此惰性补齐。
    使用 create（而非 update_or_create）避免并发覆盖既有密钥。
    """
    if (Option.objects.get_value("ygg_private_key") or "").strip():
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from django.db import IntegrityError

    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    try:
        Option.objects.create(name="ygg_private_key", value=private_pem)
        Option.objects.create(name="ygg_public_key", value=public_pem)
    except IntegrityError:
        # 并发下另一请求已生成，沿用其结果
        pass


def _public_key_pem() -> str:
    """公钥 PEM：优先 ygg_public_key 选项；缺失时从 ygg_private_key 实时派生。"""
    public_key = (Option.objects.get_value("ygg_public_key") or "").strip()
    if public_key:
        return public_key
    private_pem = (Option.objects.get_value("ygg_private_key") or "").strip()
    if not private_pem:
        return ""
    try:
        from cryptography.hazmat.primitives import serialization

        private_key = serialization.load_pem_private_key(
            private_pem.encode(), password=None
        )
        return private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
    except (ValueError, TypeError):
        return ""


def get_public_keys(request):
    """minecraftservices/publickeys（对照原版 ConfigController::getPublicKeys）。

    公钥以 DER 的 Base64 返回（去 PEM 头尾与换行），两组键组均填同一公钥。
    注：authlib-injector 客户端会本地拦截此端点、并不实际请求（PublickeysFilter），
    此处仅为结构与 PHP 原版保真。
    """
    pem = _public_key_pem()
    keys = []
    if pem:
        body = (
            pem.replace("-----BEGIN PUBLIC KEY-----", "")
            .replace("-----END PUBLIC KEY-----", "")
            .replace("\n", "")
            .strip()
        )
        keys = [{"publicKey": body}]
    return _json_ok({"profilePropertyKeys": keys, "playerCertificateKeys": keys})


# ---- authenticate ----


@csrf_exempt
@require_http_methods(["POST"])
def authenticate(request):
    if body_err := check_body_size(request):
        return body_err
    if ctype_err := check_content_type(request):
        return ctype_err
    payload = _body(request)
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""

    if not username or not password:
        return error_response(
            400, "IllegalArgumentException", "账号或密码为空"
        )

    # 规范 L492：限制针对用户而非仅 IP；先查 username 维度节流（403）
    if throttle_err := throttle_by_ip(request, username=username):
        return throttle_err

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
    # 独立开关：无论注册是否要求验证，均禁止未验证用户使用 Yggdrasil API
    if (Option.objects.get_value("unverified_block_ygg") or "0").strip().lower() in (
        "1", "true", "on",
    ) and not user.verified:
        return error_response(
            403, "ForbiddenOperationException", "邮箱未验证，无法通过启动器登录"
        )

    # clientToken 原样返回，缺省生成；超长拒绝（DB 列 255，避免 MariaDB 严格模式 500）
    client_token = payload.get("clientToken") or secrets.token_hex(16)
    if len(str(client_token)) > 255:
        return error_response(
            400, "IllegalArgumentException", "clientToken 过长（上限 255 字符）"
        )

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

    # P3-32: 清理该用户已过刷新窗口的失效令牌行
    # （原版令牌存 Cache 由 TTL 自动过期，DB 持久化需在登录时显式回收）
    for stale in YggdrasilToken.objects.filter(owner=user):
        if not stale.is_refreshable():
            stale.delete()

    # P1-13: 令牌上限（ygg_tokens_limit，默认 10；超限回收最旧）
    try:
        limit = int(Option.objects.get_value("ygg_tokens_limit") or 10)
    except ValueError:
        limit = 10
    token = YggdrasilToken.objects.create(
        owner=user,
        client_token=client_token,
        profile_id="",
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
    )
    # P1-9: 单角色才自动选中；多角色不预绑定（角色名登录则绑所用角色）
    bound_profile_id = ""
    if len(profiles) == 1:
        bound_profile_id = profiles[0]["id"]
    elif profiles and "@" not in (payload.get("username") or ""):
        # 用户名登录：绑定登录时所用的角色（原版 AuthController.php:59-64）
        identification = (payload.get("username") or "").strip()
        used = next((p for p in profiles if p["name"] == identification), None)
        if used:
            bound_profile_id = used["id"]
    token.profile_id = bound_profile_id
    ygg_token_uuid = secrets.token_hex(16)
    access_token = issue_access_token(
        user_uuid, ygg_token_uuid, expires_in, spr=bound_profile_id
    )
    token.access_token = access_token
    token.save()
    excess = YggdrasilToken.objects.filter(owner=user).order_by("-id")
    if excess.count() > limit:
        for old in excess[limit:]:
            old.delete()

    response_payload = {
        "accessToken": access_token,
        "clientToken": client_token,
        "availableProfiles": profiles,
    }
    if payload.get("requestUser"):
        response_payload["user"] = {"id": user_uuid, "properties": []}
    if len(profiles) == 1 or token.profile_id:
        selected = next((p for p in profiles if p["id"] == token.profile_id), None)
        if selected:
            response_payload["selectedProfile"] = selected

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
    if ctype_err := check_content_type(request):
        return ctype_err
    if err := check_body_size(request):
        return err
    payload = _body(request)
    access_token = payload.get("accessToken") or ""
    client_token = payload.get("clientToken")

    row = YggdrasilToken.objects.filter(access_token=access_token).first()
    if row is None or not row.is_refreshable():
        return error_response(403, "ForbiddenOperationException", "令牌无效或已过期")

    # 原版 AuthController.php:184-189：刷新时按当前配置重算有效期/刷新窗口
    try:
        row.expires_in = int(
            Option.objects.get_value("ygg_token_expire_1") or row.expires_in
        )
        row.refresh_expires_in = int(
            Option.objects.get_value("ygg_token_expire_2") or row.refresh_expires_in
        )
    except ValueError:
        pass

    user = row.owner
    if user.permission == User.Permission.BANNED:
        return error_response(403, "ForbiddenOperationException", "账号已被封禁")

    if client_token and row.client_token != client_token:
        return error_response(403, "ForbiddenOperationException", "clientToken 不匹配")

    user_uuid = user_uuid_from_email(user.email)
    profiles = [{"id": p.uuid, "name": p.name} for p in user.players.all()]

    selected = payload.get("selectedProfile")
    if selected and isinstance(selected, dict):
        # P1-7: 已绑定角色时不允许改绑（规范 L524 应 400）
        if row.profile_id and row.profile_id != selected.get("id"):
            return error_response(
                400, "IllegalArgumentException",
                "Access token already has a profile assigned.",
            )
        target = next((p for p in profiles if p["id"] == selected.get("id")), None)
        if target is None:
            # 规范错误码表：向令牌绑定不属于其用户的角色 → 403（非标准）
            return error_response(
                403, "ForbiddenOperationException", "角色不属于该用户"
            )
        row.profile_id = target["id"]

    # 轮换：新 accessToken（旧 token 随覆盖即刻失效）
    # P0-4: 刷新时重置签发时间（有效期/刷新窗口重新起算）
    from django.utils import timezone as _tz

    new_access = issue_access_token(
        user_uuid, secrets.token_hex(16), row.expires_in, spr=row.profile_id
    )
    row.access_token = new_access
    row.created_at = _tz.now()
    # selectedProfile 选角可能更新了 profile_id（P0-1：漏存会导致
    # 多角色用户 refresh 选角后 join 因 DB 仍空绑定而 403）
    row.save(update_fields=[
        "access_token", "created_at", "profile_id",
        "expires_in", "refresh_expires_in",
    ])

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
    if ctype_err := check_content_type(request):
        return ctype_err
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
    _log(request, "validate", user=row.owner)
    return HttpResponse(status=204)


@csrf_exempt
@require_http_methods(["POST"])
def invalidate(request):
    if ctype_err := check_content_type(request):
        return ctype_err
    payload = _body(request)
    access_token = payload.get("accessToken") or ""
    deleted, _ = YggdrasilToken.objects.filter(access_token=access_token).delete()
    # 原版语义：无论是否成功均 204；附审计日志（原版 ygg_log invalidate）
    _log(request, "invalidate", params={"revoked": deleted})
    return HttpResponse(status=204)


@csrf_exempt
@require_http_methods(["POST"])
def signout(request):
    if ctype_err := check_content_type(request):
        return ctype_err
    payload = _body(request)
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    if throttle_err := throttle_by_ip(request, username=username):
        return throttle_err

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
    if row.profile_id != selected_profile:
        # P1-8: 空绑定也不放行（规范 L618：selectedProfile 必须与绑定角色一致）
        return error_response(403, "ForbiddenOperationException", "令牌未选中该角色")
    if player.uid.permission == User.Permission.BANNED:
        return error_response(403, "ForbiddenOperationException", "账号已被封禁")

    # 会话登记（cache，120s，对照原版）：记录角色 UUID 与客户端 IP，
    # hasJoined 带 ip 参数时须与 join 时一致（原版 SessionController.php:32/78）
    session_key = f"ygg_server:{server_id}:{player.name}"
    cache.set(
        session_key, {"uuid": player.uuid, "ip": client_ip(request)}, 120
    )

    _log(request, "join", user=player.uid, player=player, params={"serverId": server_id})
    return HttpResponse(status=204)


@require_GET
def has_joined(request):
    username = request.GET.get("username") or ""
    server_id = request.GET.get("serverId") or ""

    session_key = f"ygg_server:{server_id}:{username}"
    cached = cache.get(session_key)
    if not cached:
        # 正版验证透传：正版客户端（join 到 Mojang）进 authlib-injector 服务器时，
        # 本地无会话——转发上游确认后再放行；关闭/失败/未绑定维持 204
        from oauth_bridge.passthrough import try_passthrough

        passthrough_resp = try_passthrough(request, username, server_id)
        if passthrough_resp is not None:
            return passthrough_resp
        return HttpResponse(status=204)
    # 会话值：新格式 {uuid, ip}；兼容滚动部署期间的旧格式纯 uuid 字符串
    if isinstance(cached, dict):
        if request.GET.get("ip") and request.GET.get("ip") != cached.get("ip"):
            # 规范 L641 / 原版 SessionController.php:117：IP 不符视为未加入
            return HttpResponse(status=204)
        player_uuid = cached.get("uuid")
    else:
        player_uuid = cached
    if not player_uuid:
        return HttpResponse(status=204)

    player = Player.objects.filter(uuid=player_uuid).select_related(
        "uid", "tid_skin", "tid_cape"
    ).first()
    if player is None or player.name != username:
        return HttpResponse(status=204)

    try:
        profile = _profile_payload(request, player, signed=True)
    except ValueError as exc:
        # 原版私钥无效时抛 IllegalArgumentException（这里同样 400）
        return error_response(400, "IllegalArgumentException", str(exc))
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
    try:
        return _json_ok(_profile_payload(request, player, signed=not unsigned))
    except ValueError as exc:
        return error_response(400, "IllegalArgumentException", str(exc))


def _profile_payload(request, player: Player, signed: bool) -> dict:
    """Profile 序列化（对照原版 Profile::serialize）。

    textures property：base64(JSON{timestamp, profileId, profileName,
    signatureRequired, textures})；signed 时对**每个**属性签名
    （原版 L112-121 foreach properties 签名），算法 SHA1withRSA。
    """
    import time as _time

    textures = {
        "timestamp": int(_time.time() * 1000),
        "profileId": player.uuid,
        "profileName": player.name,
        "isPublic": True,
        "textures": {},
    }
    # 原版 L48-56：要求签名时在 textures JSON 内标记（非 properties 元素上）
    if signed:
        textures["signatureRequired"] = True
    base_url = _public_base_url(request)
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
        },
        {
            # P1-5: 缺此属性客户端认为不可上传（规范 L216-227）
            "name": "uploadableTextures",
            "value": "skin,cape",
        },
    ]
    if signed:
        # 原版 L112-121：给每个 property 签名（客户端/Minecraft 逐个验证）
        for prop in properties:
            prop["signature"] = _sign_textures(prop["value"])

    return {
        "id": player.uuid,
        "name": player.name,
        "properties": properties,
    }


def _sign_textures(value_b64: str) -> str:
    """RSA-SHA1 签名（规范 L187 SHA1withRSA；原版 openssl_sign 默认 SHA1）。

    原版私钥无效时抛 IllegalArgumentException；此处抛 ValueError
    由调用方转为 400（保证签名返回的 properties 必带有效签名）。
    """
    _ensure_signing_key()
    pem = (Option.objects.get_value("ygg_private_key") or "").strip()
    if not pem:
        raise ValueError("缺少 Yggdrasil 签名私钥")

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(pem.encode(), password=None)
    signature = key.sign(
        value_b64.encode(), padding.PKCS1v15(), hashes.SHA1()
    )
    return base64.b64encode(signature).decode()


# ---- Profile 批量/单个查询 ----


@csrf_exempt
@require_http_methods(["POST"])
def search_profiles(request):
    names = _body(request)
    if not isinstance(names, list):
        return error_response(400, "IllegalArgumentException", "请求体须为角色名数组")
    # 元素须为字符串（非字符串直接丢给 ORM 会 500）
    if any(not isinstance(n, str) for n in names):
        return error_response(400, "IllegalArgumentException", "角色名须为字符串")
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
        return error_response(401, "ForbiddenOperationException", "无效的访问令牌")

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

    # 体积防护：multipart（PUT 时 Django 不解析 FILES，按 Content-Type 判定）
    # 不走 1MiB 原始 body 上限——否则高清材质会被 413 误杀；
    # 解析前按 CONTENT_LENGTH 预检（含 8KB 编码开销），文件本体再单查一次
    file_name = ""
    ctype = (request.headers.get("Content-Type") or "").lower()
    is_multipart = "multipart/form-data" in ctype
    if is_multipart:
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            declared = 0
        if declared > UPLOAD_MAX_FILE_BYTES + 8 * 1024:
            return error_response(
                413, "IllegalArgumentException",
                f"材质文件超过上限（{UPLOAD_MAX_FILE_BYTES // 1024 // 1024}MiB）"
            )
    elif not request.FILES.get("file"):
        if err := check_body_size(request):
            return err

    from textures.storage import texture_storage
    from textures.validation import validate_texture_image

    data = request.body
    texture_type = type_ if type_ == "cape" else "steve"
    model = request.GET.get("model") or request.headers.get("X-Texture-Model") or ""
    # P0-1: multipart/form-data 解析（规范 L729-733：model+file 字段）
    if is_multipart:
        if request.method == "POST":
            uploaded = request.FILES.get("file")
            data = uploaded.read() if uploaded else b""
            file_name = getattr(uploaded, "name", "") or ""
            model = request.POST.get("model") or model
        else:
            # Django 仅自动解析 POST 的 multipart；PUT 手动解析
            from io import BytesIO as _BytesIO

            from django.http.multipartparser import MultiPartParser

            parser = MultiPartParser(
                request.META, _BytesIO(request.body),
                request.upload_handlers, request.encoding,
            )
            post, files = parser.parse()
            uploaded = files.get("file")
            data = uploaded.read() if uploaded else b""
            file_name = getattr(uploaded, "name", "") or ""
            model = post.get("model") or model
    if type_ == "skin" and model == "slim":
        texture_type = "alex"

    # multipart 文件本体上限（跳过 body 限制后的兜底，防 DoS）
    if is_multipart and len(data) > UPLOAD_MAX_FILE_BYTES:
        return error_response(
            413, "IllegalArgumentException",
            f"材质文件超过上限（{UPLOAD_MAX_FILE_BYTES // 1024 // 1024}MiB）"
        )

    result = validate_texture_image(data, texture_type)
    if not result.valid:
        return error_response(400, "IllegalArgumentException", result.error)

    from textures.models import ClosetItem, Texture

    # 材质备注名：multipart 文件名去 .png 尾缀（原版 L112-113、规范 L733）；
    # 裸 body 上传无文件名，回落角色名
    name = (file_name.removesuffix(".png") if file_name else player.name)[:50]

    def _opt_int(key: str, fallback: int = 0) -> int:
        try:
            return int(Option.objects.get_value(key) or fallback)
        except ValueError:
            return fallback

    texture = Texture.objects.filter(hash=result.digest, uploader=user).first()
    if texture is None:
        import math

        # 原版 ProfileController.php:174：按原始文件字节计费（$file->getSize()），
        # 非消毒后重编码字节
        size_kb = math.ceil(len(data) / 1024)
        # 计分对照原版 L174-178：私密材质存储费 private_score_per_storage
        # + 衣柜费 score_per_closet_item；不足 → 403
        cost = _opt_int("private_score_per_storage") * size_kb + _opt_int(
            "score_per_closet_item"
        )
        if cost > user.score:
            return error_response(403, "ForbiddenOperationException", "积分不足")
        texture = Texture.objects.create(
            name=name,
            type=texture_type,
            hash=result.digest,
            size=size_kb,
            uploader=user,
            public=False,
            likes=1,  # 原版上传即自收藏（L189）
        )
        texture_storage.save_bytes(result.digest, result.normalized)
        if cost:
            user.score -= cost
            user.save(update_fields=["score"])
        ClosetItem.objects.create(owner=user, texture=texture, item_name=name)
    else:
        # 已有同 hash 材质（本人上传）：不在衣柜则补入；
        # 原版 L164-172 此分支仅校验积分不扣减，忠实移植
        if not user.closet.filter(texture=texture).exists():
            per_closet = _opt_int("score_per_closet_item")
            if per_closet > user.score:
                return error_response(
                    403, "ForbiddenOperationException", "积分不足"
                )
            ClosetItem.objects.create(owner=user, texture=texture, item_name=name)

    setattr(player, f"tid_{type_}", texture)
    player.save(update_fields=[f"tid_{type_}"])
    _log(request, f"upload_{type_}", user=user, player=player,
         params={"hash": result.digest})
    return HttpResponse(status=204)
