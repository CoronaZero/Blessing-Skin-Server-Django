"""正版验证透传：正版客户端进入 authlib-injector 服务器的 hasJoined 扩展。

场景链路：客户端走 Microsoft 正版认证（join 到 Mojang 会话服务器），而游戏
服务端注入 authlib-injector 并指向本站 → 服务端 hasJoined 到本站，本地无
会话记录。透传即以相同参数询问 Mojang 会话服务器确认会话真实存在，再以
本站私钥重签纹理后放行（仅限已绑定正版角色的用户）。

安全语义（fail-closed）：上游任何失败/超时/未绑定/封禁 → 一律 204 拒绝；
透传结果短缓存，避免同会话重连反复打上游。
"""

import base64
import hashlib
import json
import re

from django.core.cache import cache
from django.http import HttpResponse

from accounts.models import User

from . import httpclient, options
from .models import MojangBinding
from .sync import sync_binding_textures

MOJANG_HAS_JOINED = "https://sessionserver.mojang.com/session/minecraft/hasJoined"
UPSTREAM_TIMEOUT = 5  # 秒：服务端进服等待敏感，上游必须短超时
RESULT_TTL = 60  # 秒：透传结果缓存（Mojang 会话同样短时效）

_MC_UUID = re.compile(r"^[0-9a-f]{32}$")
_PT_PREFIX = "ygg_pt:"


def _cache_key(server_id: str, username: str) -> str:
    raw = f"{server_id}\n{username}".encode()
    return _PT_PREFIX + hashlib.sha256(raw).hexdigest()


def _build_profile(request, binding: MojangBinding, mc_name: str) -> dict | None:
    """构造皮肤站签名的 profile（id=正版 UUID，纹理指向本地存储）。

    签名失败（私钥缺失/无效）返回 None → 透传 fail-closed 拒绝。
    """
    import time as _time

    from yggdrasil.views import _public_base_url, _sign_textures

    base_url = _public_base_url(request)
    textures = {
        "timestamp": int(_time.time() * 1000),
        "profileId": binding.mc_uuid,
        "profileName": mc_name,
        "isPublic": True,
        "signatureRequired": True,
        "textures": {},
    }
    if binding.skin_local_hash:
        entry = {"url": f"{base_url}/textures/{binding.skin_local_hash}"}
        if binding.skin_model == "slim":
            entry["metadata"] = {"model": "slim"}
        textures["textures"]["SKIN"] = entry
    if binding.cape_local_hash:
        textures["textures"]["CAPE"] = {
            "url": f"{base_url}/textures/{binding.cape_local_hash}"
        }
    value = base64.b64encode(
        json.dumps(textures, separators=(",", ":")).encode()
    ).decode()
    try:
        signature = _sign_textures(value)
    except ValueError:
        return None
    return {
        "id": binding.mc_uuid,
        "name": mc_name,
        "properties": [
            {"name": "textures", "value": value, "signature": signature}
        ],
    }


def try_passthrough(request, username: str, server_id: str) -> HttpResponse | None:
    """本地会话 cache miss 后由 has_joined 调用；None 表示维持 204 拒绝。"""
    if not username or not server_id:
        return None
    if not options.passthrough_ready():
        return None

    req_ip = request.GET.get("ip") or ""
    key = _cache_key(server_id, username)

    cached = cache.get(key)
    if cached:
        # 请求带 ip 且与首次透传不一致 → 绕过缓存重新问上游（对齐本地会话语义）
        if req_ip and cached.get("ip") and req_ip != cached.get("ip"):
            cached = None
        else:
            return HttpResponse(
                json.dumps(cached["profile"]), content_type="application/json"
            )

    params = {"username": username, "serverId": server_id}
    if req_ip:
        params["ip"] = req_ip
    try:
        resp = httpclient.outbound_get(
            MOJANG_HAS_JOINED, params=params, timeout=UPSTREAM_TIMEOUT
        )
    except Exception:  # noqa: BLE001 - 上游任何网络异常 fail-closed
        return None
    if resp.status_code != 200:
        return None
    try:
        profile = resp.json()
        mc_uuid = str(profile.get("id") or "").replace("-", "").lower()
        mc_name = str(profile.get("name") or "")
    except (ValueError, AttributeError):
        return None
    if not _MC_UUID.match(mc_uuid) or not mc_name:
        return None

    binding = MojangBinding.objects.select_related("user").filter(
        mc_uuid=mc_uuid
    ).first()
    if binding is None:
        return None  # 未绑定：正版验证通过也不放行（需求：仅绑定过的玩家可进）
    if binding.user.permission == User.Permission.BANNED:
        return None

    # 正版改名自动同步：uuid 为绑定主键，name 过期无影响
    if mc_name != binding.mc_name:
        binding.mc_name = mc_name
        binding.save(update_fields=["mc_name"])

    # 纹理同步（变更检测；失败不阻塞进服，客户端按旧纹理/默认渲染）
    sync_binding_textures(binding, profile)

    payload = _build_profile(request, binding, mc_name)
    if payload is None:
        return None
    cache.set(key, {"profile": payload, "ip": req_ip}, RESULT_TTL)
    return HttpResponse(json.dumps(payload), content_type="application/json")
