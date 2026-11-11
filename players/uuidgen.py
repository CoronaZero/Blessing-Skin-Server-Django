"""UUID 生成算法（对照原版 yggdrasil-api Profile::generateUuidV3 与 uuid4）。"""

import hashlib
import uuid as uuid_lib


def generate_uuid_v3(name: str) -> str:
    """ygg_uuid_algorithm=v3：离线模式 UUID（与盗版服务器分配逻辑一致）。

    算法：md5('OfflinePlayer:' + name) 后按 RFC 4122 v3 修正 version/variant 位。
    """
    data = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode()).digest())
    data[6] = (data[6] & 0x0F) | 0x30  # version 3
    data[8] = (data[8] & 0x3F) | 0x80  # variant 10xx
    return data.hex()


def generate_uuid_v4() -> str:
    """ygg_uuid_algorithm=v4：随机 UUID（原版默认）。"""
    return uuid_lib.uuid4().hex
