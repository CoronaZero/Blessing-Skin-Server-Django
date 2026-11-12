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
    4. 重编码消毒（去元数据 / 潜在负载）并计算像素级 hash（L243-254）

    返回 ValidationResult（valid=False 时 error 给出中文原因）。
    """
    try:
        img = Image.open(io.BytesIO(data))
        # 规范 L391-397：先读 PNG 头部尺寸拒绝过大图，再完整解码
        # （open 仅解析头部；大图 load 会解压全部像素造成内存/时间放大）
        width, height = img.size
        max_width = 8192
        max_height = 8192
        if width > max_width or height > max_height:
            return ValidationResult(
                valid=False, error=f"图片宽度超过上限（{max_width}px）"
            )
        img.load()
    except Exception:
        return ValidationResult(valid=False, error="不是有效的 PNG 图片")

    if img.format != "PNG":
        return ValidationResult(valid=False, error="仅支持 PNG 格式")

    width, height = img.size

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
    # 材质 hash 采用规范定义的像素级 SHA-256（L243-254）：
    # 与 PNG 编码方式无关，同像素即同 hash，客户端可跨站复用缓存。
    # 旧数据仍按文件级 hash 保存（URL 不变），仅新上传采用新算法。
    digest = _pixel_digest(sanitized)

    return ValidationResult(
        valid=True,
        normalized=normalized,
        digest=digest,
        width=width,
        height=height,
    )


def _pixel_digest(img) -> str:
    """规范材质 hash（服务端技术规范 L243-254）。

    缓冲区布局：8 字节头（width/height 大端）+ 逐像素 4 字节 A/R/G/B，
    像素按列主序排列（x 外层、y 内层）；全透明像素 RGB 置零。
    取 SHA-256 hex。分块流式处理控制大图内存峰值。
    """
    import struct

    width, height = img.size
    # 转置后行主序即原图列主序
    transposed = img.transpose(Image.Transpose.TRANSPOSE)
    tw, th = transposed.size  # tw == height, th == width

    digest = hashlib.sha256()
    digest.update(struct.pack(">II", width, height))

    band_rows = 64  # 每批处理的转置图行数（即原图列数）
    black = None
    for top in range(0, th, band_rows):
        part = transposed.crop((0, top, tw, min(top + band_rows, th)))
        alpha_img = part.getchannel("A")
        a = alpha_img.tobytes()
        if black is None or black.size != part.size:
            black = Image.new("L", part.size, 0)
        # Alpha 为 0 的像素 RGB 置零（规范 L250）
        mask = alpha_img.point(lambda v: 255 if v else 0)
        r = Image.composite(part.getchannel("R"), black, mask).tobytes()
        g = Image.composite(part.getchannel("G"), black, mask).tobytes()
        b = Image.composite(part.getchannel("B"), black, mask).tobytes()
        buf = bytearray(len(a) * 4)
        buf[0::4] = a
        buf[1::4] = r
        buf[2::4] = g
        buf[3::4] = b
        digest.update(buf)
    return digest.hexdigest()
