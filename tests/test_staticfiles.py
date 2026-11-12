"""collectstatic 回归：静态资源必须能过 whitenoise manifest 后处理。

背景：BS5 的 min.css/js 末尾带 sourceMappingURL 注释，manifest 存储
post-process 时要求被引用的 .map 文件真实存在——漏下map会导致生产
部署 collectstatic 直接失败（容器重启循环）。
"""

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


class TestCollectstaticManifest:
    def test_collectstatic_manifest_storage(self, tmp_path, settings):
        settings.STATIC_ROOT = str(tmp_path / "static")
        settings.STORAGES = {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
            },
        }
        # 缺 sourcemap / 相对路径坏引用时抛 MissingFileError
        call_command("collectstatic", interactive=False, verbosity=0)

        # 关键 vendor 文件已就位（含 map）
        for name in (
            "vendor/bootstrap/bootstrap.min.css",
            "vendor/bootstrap/bootstrap.min.css.map",
            "vendor/bootstrap/bootstrap.bundle.min.js.map",
            "vendor/bootstrap/fonts/bootstrap-icons.woff2",
        ):
            assert (tmp_path / "static" / name).is_file(), f"缺少 {name}"
