"""正版角色纹理同步：从 Mojang profile 拉取皮肤/披风存入本地纹理存储。

- 上游 URL 恒为 textures.minecraft.net/texture/<hash>，hash 从 profile
  properties 中解析后**重新拼 canonical URL**，绝不直接请求上游给的任意 URL；
- 本地文件名用内容 sha256（与站内纹理存储命名一致），不建 Texture 记录
  （不占积分、不进皮肤库，仅作为透传签名的纹理载体）；
- 同步 best-effort：失败不阻塞绑定/透传，下次再试。
"""

import base64
import hashlib
import json
import re

from django.utils import timezone

from . import httpclient
from .models import MojangBinding

TEXTURE_CDN = "http://textures.minecraft.net/texture/"
MAX_TEXTURE_BYTES = 8 * 1024 * 1024  # 与站外导入一致的体积上限
_MOJANG_HASH = re.compile(r"^[0-9a-f]{20,64}$")
_PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def _valid_hash(value: str) -> bool:
    return bool(value) and bool(_MOJANG_HASH.match(value))


def parse_mojang_textures(profile: dict) -> dict:
    """解析 profile.properties 中 textures 属性 → {"skin": {...}, "cape": {...}}。

    skin 条目含 mojang_hash 与 model（default/slim）；无对应纹理则缺键。
    """
    for prop in profile.get("properties") or []:
        if prop.get("name") != "textures":
            continue
        try:
            data = json.loads(base64.b64decode(prop.get("value") or ""))
        except (ValueError, TypeError):
            return {}
        entries = data.get("textures") or {}
        result: dict = {}
        skin = entries.get("SKIN") or {}
        skin_hash = str((skin.get("url") or "").rsplit("/", 1)[-1])
        if _valid_hash(skin_hash):
            model = "slim" if (skin.get("metadata") or {}).get("model") == "slim" \
                else "default"
            result["skin"] = {"mojang_hash": skin_hash, "model": model}
        cape = entries.get("CAPE") or {}
        cape_hash = str((cape.get("url") or "").rsplit("/", 1)[-1])
        if _valid_hash(cape_hash):
            result["cape"] = {"mojang_hash": cape_hash}
        return result
    return {}


def _download_texture(mojang_hash: str) -> bytes | None:
    """拉取单个正版纹理（体积预检 + PNG 头校验；失败返回 None）。"""
    url = TEXTURE_CDN + mojang_hash
    try:
        resp = httpclient.outbound_get(url, timeout=15)
    except Exception:  # noqa: BLE001
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.content
    except Exception:  # noqa: BLE001
        return None
    if len(data) > MAX_TEXTURE_BYTES or not data.startswith(_PNG_HEAD):
        return None
    return data


def sync_binding_textures(binding: MojangBinding, profile: dict) -> bool:
    """按 Mojang profile 同步绑定记录的皮肤/披风纹理；返回是否有变更。

    仅在（上游 hash 变化 或 本地文件缺失）时下载，幂等且省流量。
    """
    from textures.storage import texture_storage

    parsed = parse_mojang_textures(profile)
    changed = False
    skin = parsed.get("skin")
    if skin:
        need = (
            skin["mojang_hash"] != binding.skin_mojang_hash
            or not binding.skin_local_hash
            or not texture_storage.exists(binding.skin_local_hash)
        )
        if need:
            data = _download_texture(skin["mojang_hash"])
            if data is not None:
                local = hashlib.sha256(data).hexdigest()
                texture_storage.save_bytes(local, data)
                binding.skin_mojang_hash = skin["mojang_hash"]
                binding.skin_local_hash = local
                binding.skin_model = skin["model"]
                changed = True
    else:
        # 上游无皮肤：清掉本地引用（保留已下载文件，供其他绑定复用 hash 命名）
        if binding.skin_local_hash:
            binding.skin_mojang_hash = ""
            binding.skin_local_hash = ""
            changed = True

    cape = parsed.get("cape")
    if cape:
        need = (
            cape["mojang_hash"] != binding.cape_mojang_hash
            or not binding.cape_local_hash
            or not texture_storage.exists(binding.cape_local_hash)
        )
        if need:
            data = _download_texture(cape["mojang_hash"])
            if data is not None:
                local = hashlib.sha256(data).hexdigest()
                texture_storage.save_bytes(local, data)
                binding.cape_mojang_hash = cape["mojang_hash"]
                binding.cape_local_hash = local
                changed = True
    elif binding.cape_local_hash:
        binding.cape_mojang_hash = ""
        binding.cape_local_hash = ""
        changed = True

    if changed or binding.textures_synced_at is None:
        binding.textures_synced_at = timezone.now()
        binding.save(update_fields=[
            "skin_mojang_hash", "skin_local_hash", "skin_model",
            "cape_mojang_hash", "cape_local_hash", "textures_synced_at",
        ])
    return changed
