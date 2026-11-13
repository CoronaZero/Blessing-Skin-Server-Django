"""Minecraft 好友系统协议端点（`{API_ROOT}minecraftservices/*`）。

依据《Minecraft 好友系统协议——皮肤站实现规范》（authlib 10.0.77 +
Minecraft 26.3 客户端 + authlib-injector 三方交叉还原）：

- 鉴权：Yggdrasil accessToken（Bearer），复用 get_bearer_user；
  token 未绑定角色 → 403（与 join 一致）。
- 好友以 Player（角色）为顶点；Friendship 有向表双向推导入状态。
- Presence 存 Django cache（TTL 300s），仅在好友之间可见。
- GET /friends 支持 ETag/304；所有端点返回 application/json。
- feature.enable_mojang_anti_features 开关由 hello meta 下发（见 views.hello）。
- 全部端点 csrf_exempt：鉴权走 Bearer，无 Cookie 会话；authlib-injector
  代理转发 PUT/POST 时不会携带 CSRF token（否则 403 CSRF 页面）。
"""

import hashlib
import json

from django.core.cache import cache
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from players.models import Friendship, Player

from .middleware import get_bearer_user

PRESENCE_TTL = 300  # 5 分钟无上报视为离线（规范 §4.3 建议）
ZERO_UUID = "0" * 32
PRESENCE_CACHE_PREFIX = "msvc_presence:"


# ---- 通用 ----


def _json(payload, status=200):
    return HttpResponse(
        json.dumps(payload), status=status, content_type="application/json"
    )


def _friends_error(request, status, error, message, details=None):
    """规范 §3.3 错误体（path/error/errorMessage/details，application/json）。"""
    return _json(
        {
            "path": request.path,
            "error": error,
            "errorMessage": message,
            "details": details or {},
        },
        status=status,
    )


def _auth(request):
    """Bearer → (user, token_row, player)；失败返回 (None, None, error_response)。

    token 有效但未绑定角色 → 403（规范 §3.1，与 join 一致）。
    """
    user, row = get_bearer_user(request)
    if user is None:
        return None, None, _friends_error(
            request, 401, "UNAUTHORIZED", "无效的访问令牌"
        )
    player = Player.objects.filter(uuid=row.profile_id).first() if row.profile_id else None
    if player is None:
        return None, None, _friends_error(
            request, 403, "FORBIDDEN",
            "尚未绑定角色，请先在启动器中选择角色",
        )
    if player.uid_id != user.uid:
        return None, None, _friends_error(
            request, 403, "FORBIDDEN", "角色与令牌不匹配"
        )
    return user, row, player


def _uuid_key(player: Player) -> str:
    return player.uuid.lower()


def _rate_limited(player: Player) -> HttpResponse | None:
    """每用户每分钟上限（friends_rate_limit；0=不限）。429 + 纯整数 Retry-After。"""
    import time as _time

    from options.models import Option

    try:
        limit = int(Option.objects.get_value("friends_rate_limit") or 60)
    except ValueError:
        limit = 60
    if limit <= 0:
        return None
    key = f"msvc_rl:{_uuid_key(player)}:{int(_time.time() // 60)}"
    cache.add(key, 0, 70)
    try:
        count = cache.incr(key)
    except ValueError:
        count = 1
    if count > limit:
        resp = _json(
            {"error": "TOO_MANY_REQUESTS", "errorMessage": "请求过于频繁"},
            status=429,
        )
        resp["Retry-After"] = "60"
        return resp
    return None


def _norm_uuid(raw) -> str:
    """32 位 hex（兼容带连字符），非法返回空串。"""
    if not isinstance(raw, str):
        return ""
    value = raw.replace("-", "").strip().lower()
    if len(value) == 32 and all(c in "0123456789abcdef" for c in value):
        return value
    return ""


def _friend_dto(player: Player) -> dict:
    return {"profileId": _uuid_key(player), "name": player.name}


def _friends_snapshot(player: Player) -> dict:
    """三集合快照（规范 §4.1）：friends / incomingRequests / outgoingRequests。"""
    sent = Friendship.objects.filter(requester=player).select_related("addressee")
    received = Friendship.objects.filter(addressee=player).select_related("requester")
    sent_map = {f.addressee_id: f.addressee for f in sent}
    received_map = {f.requester_id: f.requester for f in received}

    friends = [
        _friend_dto(other)
        for pid, other in sent_map.items()
        if pid in received_map
    ]
    incoming = [_friend_dto(other) for pid, other in received_map.items() if pid not in sent_map]
    outgoing = [_friend_dto(other) for pid, other in sent_map.items() if pid not in received_map]
    return {
        "friends": friends,
        "incomingRequests": incoming,
        "outgoingRequests": outgoing,
    }


# ---- 端点 ----


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def friends_endpoint(request):
    """GET=快照 / PUT=动作（同 URL 按方法分派）。"""
    user, _row, player = _auth(request)
    if user is None:
        return player
    if limited := _rate_limited(player):
        return limited
    if request.method == "PUT":
        return friends_update(request)
    return friends_list(request)


@csrf_exempt
@require_GET
def friends_list(request):
    """GET /friends：好友全量快照 + ETag/304（规范 §4.1）。"""
    user, _row, player = _auth(request)
    if user is None:
        return player  # error_response

    snapshot = _friends_snapshot(player)
    body = json.dumps(snapshot)
    etag = f'"{hashlib.sha1(body.encode()).hexdigest()}"'
    if request.headers.get("If-None-Match") == etag:
        resp = HttpResponse(status=304)
        resp["ETag"] = etag
        return resp
    resp = HttpResponse(body, content_type="application/json")
    resp["ETag"] = etag
    return resp


@csrf_exempt
@require_http_methods(["PUT"])
def friends_update(request):
    """PUT /friends：ADD/REMOVE 状态机（规范 §4.2），返回新快照。

    - ADD 对方已有→我的 incoming：撤销请求并直接建好友（接受申请）
    - 已是好友 / 已有 outgoing：幂等成功
    - REMOVE：删好友 / 撤回 outgoing / 拒绝 incoming / 无关系幂等
    """
    user, _row, player = _auth(request)
    if user is None:
        return player

    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _friends_error(request, 400, "BAD_REQUEST", "请求体不是有效 JSON")
    update_type = payload.get("updateType")
    if update_type not in ("ADD", "REMOVE"):
        return _friends_error(request, 400, "BAD_REQUEST", "updateType 非法")

    target = None
    name = payload.get("name")
    target_uuid = _norm_uuid(payload.get("profileId"))
    if target_uuid:
        target = Player.objects.filter(uuid=target_uuid).first()
    elif isinstance(name, str) and name.strip():
        # 大小写不敏感匹配（规范 §4.2）；返回存储的规范名
        target = Player.objects.filter(name__iexact=name.strip()).first()
    if target is None:
        return _friends_error(
            request, 400, "UNKNOWN_PROFILE", "角色不存在",
            {"status": "UNKNOWN_PROFILE"},
        )
    if target.pk == player.pk:
        return _friends_error(
            request, 400, "UNKNOWN_PROFILE", "不能添加自己为好友",
            {"status": "CANNOT_ADD_SELF"},
        )

    if update_type == "ADD":
        reverse = Friendship.objects.filter(requester=target, addressee=player).first()
        forward = Friendship.objects.filter(requester=player, addressee=target).first()
        if reverse is not None:
            # 接受对方的申请（或已是好友时 forward 也已存在）
            if forward is None:
                Friendship.objects.create(requester=player, addressee=target)
        elif forward is None:
            Friendship.objects.create(requester=player, addressee=target)
        # 已有 outgoing：幂等
    else:  # REMOVE
        Friendship.objects.filter(requester=player, addressee=target).delete()
        Friendship.objects.filter(requester=target, addressee=player).delete()

    return _json(_friends_snapshot(player))


def _presence_key(player: Player) -> str:
    return PRESENCE_CACHE_PREFIX + _uuid_key(player)


def _get_presence(player: Player) -> dict:
    return cache.get(_presence_key(player)) or {}


@csrf_exempt
@require_POST
def presence_update(request):
    """POST /presence：上报我的状态 + 取回好友状态（规范 §4.3）。"""
    user, _row, player = _auth(request)
    if user is None:
        return player
    if limited := _rate_limited(player):
        return limited

    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _friends_error(request, 400, "BAD_REQUEST", "请求体不是有效 JSON")
    status = payload.get("status")
    valid = {
        "ONLINE", "PLAYING_OFFLINE", "PLAYING_REALMS",
        "PLAYING_SERVER", "PLAYING_HOSTED_SERVER", "OFFLINE",
    }
    if status not in valid:
        return _friends_error(request, 400, "BAD_REQUEST", "status 非法")

    from django.utils import timezone

    now = timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    cache.set(
        _presence_key(player),
        {"status": status, "updated_at": now},
        PRESENCE_TTL,
    )

    # 仅好友可见（隐私边界，规范 §7.4）
    snapshot = _friends_snapshot(player)
    entries = []
    for friend in snapshot["friends"]:
        other = Player.objects.filter(uuid=friend["profileId"]).first()
        if other is None:
            continue
        entry = _get_presence(other)
        if not entry:
            continue
        entries.append({
            "profileId": _uuid_key(other),
            "pmid": ZERO_UUID,
            "status": entry["status"],
            "lastUpdated": entry["updated_at"],
        })
    return _json({"presence": entries})


def _privileges_payload() -> dict:
    return {
        "onlineChat": {"enabled": True},
        "multiplayerServer": {"enabled": True},
        "multiplayerRealms": {"enabled": True},
        "telemetry": {"enabled": False},
        "optionalTelemetry": {"enabled": False},
    }


@csrf_exempt
@require_http_methods(["GET", "POST"])
def player_attributes(request):
    """GET/POST /player/attributes：用户属性与好友设置（规范 §4.4/§4.5）。"""
    user, _row, player = _auth(request)
    if user is None:
        return player

    if request.method == "POST":
        try:
            payload = json.loads(request.body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _friends_error(request, 400, "BAD_REQUEST", "请求体不是有效 JSON")
        prefs = payload.get("friendsPreferences") or {}
        fields = []
        if isinstance(prefs.get("friends"), str):
            user.friends_enabled = prefs["friends"].upper() == "ENABLED"
            fields.append("friends_enabled")
        if isinstance(prefs.get("acceptInvites"), str):
            user.accept_invites = prefs["acceptInvites"].upper() == "ENABLED"
            fields.append("accept_invites")
        if fields:
            user.save(update_fields=fields)

    return _json({
        "privileges": _privileges_payload(),
        "profanityFilterPreferences": {"profanityFilterOn": False},
        "friendsPreferences": {
            "friends": "ENABLED" if user.friends_enabled else "DISABLED",
            "acceptInvites": "ENABLED" if user.accept_invites else "DISABLED",
        },
        "chatPreferences": {"textCommunication": "ENABLED"},
        "banStatus": {"bannedScopes": {}},
    })


@csrf_exempt
@require_GET
def privileges_legacy(request):
    """GET /privileges：老端点（规范 §4.7，与 attributes 同构）。"""
    user, _row, player = _auth(request)
    if user is None:
        return player
    return _json({"privileges": _privileges_payload()})


@csrf_exempt
@require_GET
def blocklist(request):
    """GET /privacy/blocklist：MVP 空集合（规范 §4.6）。"""
    user, _row, player = _auth(request)
    if user is None:
        return player
    return _json({"blockedProfiles": []})


@csrf_exempt
@require_POST
def events_noop(request):
    """POST /events：遥测 no-op（规范 §4.8）。"""
    return HttpResponse(status=204)


@csrf_exempt
@require_POST
def report_noop(request):
    """POST /player/report：举报 no-op（规范 §4.8）。"""
    return HttpResponse(status=204)


@csrf_exempt
@require_GET
def blocked_servers(request):
    """GET /sessionserver/blockedservers：404（复刻原 AntiFeaturesFilter 行为）。"""
    return HttpResponse(status=404)


# 说明：minecraftservices 前缀内的限频依赖 yggdrasil.middleware.throttle_by_ip
# 的通用节流（authenticate/signout 之外的端点不做强制；规范 §6 建议值可在
# 后续按实际流量启用——客户端自带轮询节奏与 10s 冷却）。
