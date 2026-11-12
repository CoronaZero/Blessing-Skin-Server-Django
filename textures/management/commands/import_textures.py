"""批量导入纹理：python manage.py import_textures <目录> [--uploader 邮箱或昵称]。

对照原 batch-import 插件语义：
- 扫描目录中的 PNG，按文件名归并同名 steve/alex/cape（后缀约定）
- 走完整校验消毒管线（同上传 API）
- --uploader 支持邮箱或昵称指定归属用户：先精确匹配，
  昵称模糊匹配时要求唯一命中；缺省时默认第一位管理员
"""

import math
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from accounts.models import User
from textures.models import Texture
from textures.storage import texture_storage
from textures.validation import validate_texture_image


def _resolve_uploader(raw: str):
    """解析上传者：邮箱（不区分大小写）→ 昵称精确 → 昵称模糊唯一。

    返回 (user, error)；error 非 None 时 user 为 None，
    多命中时提示候选以便改用邮箱精确指定。
    """
    raw = raw.strip()
    exact = User.objects.filter(
        Q(email__iexact=raw) | Q(nickname=raw)
    ).first()
    if exact:
        return exact, None

    matched = User.objects.filter(nickname__icontains=raw)
    count = matched.count()
    if count == 1:
        return matched.first(), None
    if count == 0:
        return None, f"未找到匹配的用户（邮箱或昵称）：{raw}"
    sample = "、".join(f"{u.nickname} <{u.email}>" for u in matched[:5])
    return None, (
        f"昵称「{raw}」匹配到 {count} 位用户，请改用邮箱精确指定。"
        f"候选：{sample}"
    )


class Command(BaseCommand):
    help = "从目录批量导入纹理（对照原 batch-import 插件）"

    def add_arguments(self, parser):
        parser.add_argument("directory", help="包含 PNG 文件的目录")
        parser.add_argument(
            "--uploader", default="",
            help="上传者邮箱或昵称（昵称模糊匹配须唯一；默认第一位管理员）",
        )
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
            uploader, error = _resolve_uploader(options["uploader"])
            if uploader is None:
                raise CommandError(error)
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
        self.stdout.write(f"上传者：{uploader.nickname} <{uploader.email}>")
