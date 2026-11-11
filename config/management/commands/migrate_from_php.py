"""从 PHP 原版数据库迁移数据（对照 blessing-skin-server-php schema）。

用法：
    python manage.py migrate_from_php --source "mysql://user:pass@host/db"
    python manage.py migrate_from_php --source "sqlite:///path/to/db.sqlite3"

设计：
- 旧库只读接入（SQLite 走标准库 sqlite3 直连，MySQL/PgSQL 走动态
  Django alias），Django ORM 不做双库关联；
- 逐表映射搬运，按业务键 upsert → 可重跑（不产生重复行）；
- 密码散列原样搬运，登录时由 accounts.hashers 兼容层校验并自动升级；
- 皮肤文件零搬动（hash 不变，直接指向原存储根目录）；
- 结束输出逐表行数比对报告。
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.utils import timezone

from accounts.models import User
from moderation.models import Report
from notifications.models import Notification
from options.models import Option
from players.models import Player
from textures.models import ClosetItem, Texture


def parse_source(dsn: str) -> dict:
    """解析 --source DSN 为连接描述（scheme + 参数）。"""
    parsed = urlparse(dsn)
    if parsed.scheme == "sqlite":
        path = parsed.path
        # Windows 盘符路径：sqlite:///C:/... 解析出前导斜杠，剥掉
        if len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        if not Path(path).exists():
            raise CommandError(f"SQLite 文件不存在：{path}")
        return {"scheme": "sqlite", "path": path}
    if parsed.scheme in ("mysql", "mariadb"):
        return {
            "scheme": "mysql",
            "HOST": parsed.hostname or "127.0.0.1",
            "PORT": str(parsed.port or 3306),
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
        }
    if parsed.scheme in ("postgres", "postgresql", "pgsql"):
        return {
            "scheme": "pgsql",
            "HOST": parsed.hostname or "127.0.0.1",
            "PORT": str(parsed.port or 5432),
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
        }
    raise CommandError(f"不支持的 DSN scheme：{parsed.scheme}（支持 sqlite/mysql/pgsql）")


class LegacyDatabase:
    """旧库只读访问门面：sqlite 直连标准库，其余走 Django 动态 alias。"""

    def __init__(self, source: dict):
        self.source = source
        self._sqlite = None
        self._alias_conn = None

    # ---- 连接 ----
    def open(self):
        if self.source["scheme"] == "sqlite":
            self._sqlite = sqlite3.connect(self.source["path"])
        else:
            alias = "php_source"
            conf = {
                "ENGINE": (
                    "django.db.backends.mysql"
                    if self.source["scheme"] == "mysql"
                    else "django.db.backends.postgresql"
                ),
                "HOST": self.source["HOST"],
                "PORT": self.source["PORT"],
                "NAME": self.source["NAME"],
                "USER": self.source["USER"],
                "PASSWORD": self.source["PASSWORD"],
            }
            settings.DATABASES[alias] = conf
            connections.databases[alias] = conf
            self._alias_conn = connections[alias]
            self._alias_conn.ensure_connection()

    def close(self):
        if self._sqlite is not None:
            self._sqlite.close()
        if self._alias_conn is not None:
            self._alias_conn.close()

    # ---- 查询 ----
    def _cursor(self):
        if self._sqlite is not None:
            return self._sqlite.cursor()
        return self._alias_conn.cursor()

    @staticmethod
    def _fetch_all(cursor, query, params):
        cursor.execute(query, params)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]

    def rows(self, table: str) -> list[dict]:
        if self._sqlite is not None:
            return self._fetch_all(self._cursor(), f"SELECT * FROM {table}", [])
        # MySQL/PgSQL：information_schema 适配的引号
        return self._fetch_all(self._cursor(), f'SELECT * FROM "{table}"', [])

    def has_table(self, table: str) -> bool:
        cursor = self._cursor()
        if self._sqlite is not None:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            )
        elif self.source["scheme"] == "mysql":
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables"
                " WHERE table_name = %s",
                [table],
            )
            return bool(cursor.fetchone()[0])
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables"
                " WHERE table_name = %s",
                [table],
            )
            return bool(cursor.fetchone()[0])
        return bool(cursor.fetchone())


def _dt(value):
    """旧库 DATETIME 字符串 → aware datetime（时区按站点配置）。"""
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                parsed = datetime.strptime(str(value), fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


class Command(BaseCommand):
    help = "从 PHP 原版（blessing-skin-server）数据库迁移数据到本系统"

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="旧库 DSN")
        parser.add_argument(
            "--table-prefix",
            default="",
            help="旧库表前缀（原版 DB_PREFIX，默认无前缀）",
        )

    def handle(self, *args, **options):
        dsn = options["source"]
        prefix = options["table_prefix"]

        legacy = LegacyDatabase(parse_source(dsn))
        try:
            legacy.open()
        except Exception as exc:  # noqa: BLE001 - 连接错误统一转译
            raise CommandError(f"无法连接旧库：{exc}") from exc

        def table(name: str) -> str:
            return prefix + name

        self.stdout.write(f"已连接旧库（{parse_source(dsn)['scheme']}）")

        stats = {}
        with transaction.atomic():
            stats["users"] = self.migrate_users(legacy.rows(table("users")))
            stats["players"] = self.migrate_players(legacy, table)
            stats["textures"] = self.migrate_textures(legacy.rows(table("textures")))
            stats["closet"] = self.migrate_closet(legacy.rows(table("user_closet")))
            stats["reports"] = self.migrate_reports(legacy.rows(table("reports")))
            stats["options"] = self.migrate_options(legacy.rows(table("options")))
            if legacy.has_table(table("notifications")):
                stats["notifications"] = self.migrate_notifications(
                    legacy.rows(table("notifications"))
                )
            else:
                self.stdout.write("（旧库无 notifications 表，跳过站内通知）")

        legacy.close()
        self.print_report(stats)
        self.stdout.write(self.style.SUCCESS("迁移完成"))

    # ---- 各表搬运 ----

    def migrate_users(self, source_rows) -> int:
        for row in source_rows:
            User.objects.update_or_create(
                uid=row["uid"],
                defaults={
                    "email": row["email"],
                    "nickname": row.get("nickname") or "",
                    "password": row["password"],  # 原样搬运，登录时兼容校验
                    "score": row.get("score") or 0,
                    "avatar": row.get("avatar") or 0,
                    "ip": row.get("ip") or "",
                    "permission": row.get("permission") or 0,
                    "verified": bool(row.get("verified")),
                    "last_sign_at": _dt(row.get("last_sign_at")),
                    "register_at": _dt(row.get("register_at")),
                },
            )
        return len(source_rows)

    def migrate_players(self, legacy, table) -> int:
        uuid_map = {}
        if legacy.has_table(table("uuid")):
            for row in legacy.rows(table("uuid")):
                uuid_map[row["name"]] = row["uuid"]

        from players.uuidgen import generate_uuid_v4

        for row in legacy.rows(table("players")):
            name = row["player_name"]
            Player.objects.update_or_create(
                pid=row["pid"],
                defaults={
                    "uid_id": row["uid"],
                    "name": name,
                    "uuid": uuid_map.get(name) or generate_uuid_v4(),
                    "tid_skin_id": row.get("tid_skin") or None,
                    "tid_cape_id": row.get("tid_cape") or None,
                    "last_modified": _dt(row.get("last_modified")),
                },
            )
        return len(uuid_map) and len(legacy.rows(table("players")))

    def migrate_textures(self, source_rows) -> int:
        for row in source_rows:
            Texture.objects.update_or_create(
                tid=row["tid"],
                defaults={
                    "name": row["name"],
                    "type": row["type"],
                    "hash": row["hash"],
                    "size": row.get("size") or 0,
                    "uploader_id": row["uploader"],
                    "public": bool(row.get("public")),
                    "likes": row.get("likes") or 0,
                    "upload_at": _dt(row.get("upload_at")),
                },
            )
        return len(source_rows)

    def migrate_closet(self, source_rows) -> int:
        for row in source_rows:
            ClosetItem.objects.update_or_create(
                owner_id=row["user_uid"],
                texture_id=row["texture_tid"],
                defaults={"item_name": row.get("item_name") or ""},
            )
        return len(source_rows)

    def migrate_reports(self, source_rows) -> int:
        for row in source_rows:
            Report.objects.update_or_create(
                id=row["id"],
                defaults={
                    "texture_id": row["tid"],
                    "uploader_id": row["uploader"],
                    "reporter_id": row["reporter"],
                    "reason": row["reason"],
                    "status": row.get("status") or 0,
                    "report_at": _dt(row.get("report_at")),
                },
            )
        return len(source_rows)

    def migrate_options(self, source_rows) -> int:
        count = 0
        for row in source_rows:
            value = row.get("option_value")
            if value is not None:
                Option.objects.set(row["option_name"], value)
                count += 1
        return count

    def migrate_notifications(self, source_rows) -> int:
        """Laravel notifications：notifiable_id = 收件用户，data JSON 拆 title。

        update_or_create 键：(recipient, title, read_at) —— 历史数据去重语义，
        重跑不重复。created_at 为 auto_now_add，无法保留旧时间戳（可接受）。
        """
        for row in source_rows:
            try:
                data = json.loads(row.get("data") or "{}")
            except json.JSONDecodeError:
                data = {}
            title = data.get("title") or data.get("subject") or row.get("type") or "通知"
            Notification.objects.update_or_create(
                recipient_id=row["notifiable_id"],
                title=title,
                read_at=_dt(row.get("read_at")),
                defaults={
                    "content": data.get("body") or data.get("content") or "",
                },
            )
        return len(source_rows)

    def print_report(self, stats: dict):
        self.stdout.write("---- 行数报告 ----")
        new_counts = {
            "users": User.objects.count(),
            "players": Player.objects.count(),
            "textures": Texture.objects.count(),
            "closet": ClosetItem.objects.count(),
            "reports": Report.objects.count(),
        }
        for name, migrated in stats.items():
            extra = ""
            if name in new_counts:
                extra = f"（新库共 {new_counts[name]}）"
            self.stdout.write(f"  {name}: 搬运 {migrated} 行{extra}")
