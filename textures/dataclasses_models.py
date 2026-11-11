"""纹理模块数据结构（独立文件避免 models 依赖环）。"""

from dataclasses import dataclass


@dataclass
class ValidationResult:
    """纹理上传校验结果。"""

    valid: bool
    error: str = ""
    normalized: bytes = b""
    digest: str = ""
    width: int = 0
    height: int = 0
