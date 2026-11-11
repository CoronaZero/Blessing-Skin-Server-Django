"""纹理文件存储：sha256 命名、无扩展名、root 平铺（对照原版存储布局）。

设计为可替换后端（Phase 5 若接 OSS/COS，实现同接口即可）。
"""

from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage


class TextureFileNotFoundError(Exception):
    pass


class TextureStorage:
    """本地文件系统实现（默认）。"""

    def __init__(self):
        # 延迟初始化的 storage（测试中替换为 tmp 目录）
        self._storage: Storage | None = None

    @property
    def storage(self) -> Storage:
        if self._storage is None:
            default_root = str(Path(settings.BASE_DIR) / "storage" / "textures")
            root = getattr(settings, "TEXTURE_STORAGE_ROOT", default_root)
            self._storage = FileSystemStorage(location=root)
        return self._storage

    # ---- 校验文件名（防路径穿越） ----
    @staticmethod
    def _check(hash_value: str) -> None:
        if len(hash_value) != 64 or not all(c in "0123456789abcdef" for c in hash_value):
            raise ValueError("非法的纹理 hash")

    def exists(self, hash_value: str) -> bool:
        self._check(hash_value)
        return self.storage.exists(hash_value)

    def save_bytes(self, hash_value: str, data: bytes) -> None:
        self._check(hash_value)
        if self.storage.exists(hash_value):
            # 幂等：hash 相同内容必相同，直接覆盖
            self.storage.delete(hash_value)
        self.storage.save(hash_value, _BytesFile(hash_value, data))

    def read(self, hash_value: str) -> bytes:
        self._check(hash_value)
        if not self.storage.exists(hash_value):
            raise TextureFileNotFoundError(hash_value)
        return self.storage.open(hash_value, "rb").read()

    def delete(self, hash_value: str) -> None:
        self._check(hash_value)
        if self.storage.exists(hash_value):
            self.storage.delete(hash_value)

    def path(self, hash_value: str) -> str:
        self._check(hash_value)
        return self.storage.path(hash_value)

    def size(self, hash_value: str) -> int:
        self._check(hash_value)
        return self.storage.size(hash_value)


class _BytesFile:
    """把 bytes 包装成 Django storage 认的 file-like。"""

    def __init__(self, name: str, data: bytes):
        import io

        self._file = io.BytesIO(data)
        self.name = name

    def read(self, *args):
        return self._file.read(*args)

    def seek(self, *args):
        return self._file.seek(*args)

    def tell(self):
        return self._file.tell()

    @property
    def size(self):
        return self._file.getbuffer().nbytes


texture_storage = TextureStorage()
