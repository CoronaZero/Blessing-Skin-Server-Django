"""批量导入纹理：python manage.py import_textures <目录> [--uploader email]。

对照原 batch-import 插件语义：
- 扫描目录中的 PNG，按文件名归并同名 steve/alex/cape（后缀约定）
- 走完整校验消毒管线（同上传 API）
- 上传者默认第一位管理员
"""

import math
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from textures.models import Texture
from textures.storage import texture_storage
from textures.validation import validate_texture_image


class Command(BaseCommand):
    help = "从目录批量导入纹理（对照原 batch-import 插件）"

    def add_arguments(self, parser):
        parser.add_argument("directory", help="包含 PNG 文件的目录")
        parser.add_argument("--uploader", default="", help="上传者邮箱（默认第一位管理员）")
        parser.add_argument("--public", action="store_true", help="导入为公开纹理")
        parser.add_argument("--dry-run", action="store_true", help="只检查不写入")

    def handle(self, *args, **options):
        directory = Path(options["directory"])
        if not directory.is_dir():
            raise CommandError(f"目录不存在：{directory}")

        pngs = sorted(directory.glob("*.png"))
        if not pngs:
            self.stdout.write("目录中没有 PNG 文件")
            return

        if options["uploader"]:
            uploader = User.objects.filter(email=options["uploader"]).first()
        else:
            uploader = (
                User.objects.filter(permission__gte=User.Permission.ADMIN)
                .order_by("uid")
                .first()
            )
        if uploader is None:
            raise CommandError("未找到上传者（--uploader 或先创建管理员）")

        ok, skipped, invalid = 0, 0, 0
        for png in pngs:
            # 命名约定：<名称>.png / <名称>.alex.png / <名称>.cape.png
            stem = png.stem
            if stem.endswith(".alex"):
                name, texture_type = stem.removesuffix(".alex"), "alex"
            elif stem.endswith(".cape"):
                name, texture_type = stem.removesuffix(".cape"), "cape"
            else:
                name, texture_type = stem, "steve"

            data = png.read_bytes()
            result = validate_texture_image(data, texture_type)
            if not result.valid:
                self.stdout.write(self.style.WARNING(f"  跳过 {png.name}：{result.error}"))
                invalid += 1
                continue

            if Texture.objects.filter(hash=result.digest).exists():
                self.stdout.write(f"  已存在 {png.name}（hash 重复）")
                skipped += 1
                continue

            if options["dry_run"]:
                self.stdout.write(f"  [dry-run] 将导入 {png.name} → {texture_type}")
                ok += 1
                continue

            Texture.objects.create(
                name=name,
                type=texture_type,
                hash=result.digest,
                size=math.ceil(len(result.normalized) / 1024),
                uploader=uploader,
                public=options["public"],
            )
            texture_storage.save_bytes(result.digest, result.normalized)
            ok += 1

        summary = f"导入 {ok}，跳过（重复）{skipped}，无效 {invalid}"
        if options["dry_run"]:
            summary = "[dry-run] " + summary
        self.stdout.write(self.style.SUCCESS(summary))
        self.stdout.write(f"上传者：{uploader.email}")
