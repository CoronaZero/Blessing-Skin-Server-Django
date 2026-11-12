"""纹理文件校验：格式/尺寸/比例（对照原版 SkinlibController::upload 链）。"""

import hashlib
import io

from PIL import Image

from .dataclasses_models import ValidationResult


def validate_texture_image(data: bytes, texture_type: str) -> ValidationResult:
    """校验 + 消毒。

    步骤（对照原版 + 规范 L391-397）：
    1. PIL 解码（非 PNG / 损坏 → 拒绝）
    2. 宽 % 64 == 0 且 高 % 32 == 0
    3. 比例校验：
       - steve: ratio 2 或 1
       - alex:  ratio 1（2 被拒绝）
       - cape:  64x32 整数倍，或 22x17 整数倍（遗留尺寸，
         须透明补足至 64x32 整数倍——规范 L395）
    4. 重编码消毒（去元数据 / 潜在负载）并计算 hash

    返回 ValidationResult（valid=False 时 error 给出中文原因）。
    """
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return ValidationResult(valid=False, error="不是有效的 PNG 图片")

    if img.format != "PNG":
        return ValidationResult(valid=False, error="仅支持 PNG 格式")

    width, height = img.size
    max_width = 8192
    max_height = 8192
    # 规范 L391-397：先查尺寸拒绝过大图（PNG Bomb），width/height 都查
    if width > max_width or height > max_height:
        return ValidationResult(
            valid=False, error=f"图片宽度超过上限（{max_width}px）"
        )

    if texture_type in ("steve", "alex"):
        if width % 64 != 0 or height % 32 != 0:
            return ValidationResult(
                valid=False, error="宽度须为 64 的倍数，高度须为 32 的倍数"
            )
        ratio = width / height
        if ratio not in (2.0, 1.0):
            return ValidationResult(valid=False, error="皮肤长宽比不正确")
        if texture_type == "alex" and ratio == 2.0:
            return ValidationResult(valid=False, error="皮肤长宽比不正确")
    elif texture_type == "cape":
        # 规范 L395：披风为 64x32 整数倍或 22x17 整数倍；
        # 22x17 遗留比例非标准尺寸，须透明补足至 64x32 整数倍
        if width % 64 == 0 and height % 32 == 0:
            pass
        elif (
            width % 22 == 0
            and height % 17 == 0
            and width // 22 == height // 17
        ):
            padded = Image.new(
                "RGBA",
                (((width + 63) // 64) * 64, ((height + 31) // 32) * 32),
                (0, 0, 0, 0),
            )
            padded.paste(img, (0, 0))
            img = padded
            width, height = img.size
        else:
            return ValidationResult(valid=False, error="披风长宽比不正确")
    else:
        return ValidationResult(valid=False, error=f"未知的纹理类型：{texture_type}")

    # 重编码消毒：统一 RGBA、剥离全部元数据（对照原版 Imagick stripImage）
    sanitized = img.convert("RGBA") if img.mode != "RGBA" else img.copy()
    buf = io.BytesIO()
    sanitized.save(buf, format="PNG", optimize=True)
    normalized = buf.getvalue()
    digest = hashlib.sha256(normalized).hexdigest()

    return ValidationResult(
        valid=True,
        normalized=normalized,
        digest=digest,
        width=width,
        height=height,
    )
