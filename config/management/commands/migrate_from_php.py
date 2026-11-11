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

import hashlib
import json
import os
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


def parse_php_env_file(env_path: Path) -> dict:
    """解析 PHP 站 .env（Laravel dotenv 格式：KEY=VALUE，# 注释）。

    只提取迁移相关的键，容忍引号与行内注释。
    """
    result = {}
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        # 去引号
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # Laravel .env 值内 # 常见，保守不做行内注释剥离
        if value.lower() in ("null", "true", "false", "empty"):
            value = {"null": "", "true": "1", "false": "", "empty": ""}.get(
                value.lower(), value
            )
        result[key] = value
    return result


def build_dsn_from_env(php_env: dict, env_path: Path) -> tuple:
    """从 .env 组装 (source 描述, 皮肤目录, 站点根目录)。

    对照原版 config/secure.php、config/database.php、config/filesystems.php：
    - DB_CONNECTION（mysql/pgsql/sqlite）+ DB_HOST/DB_PORT/DB_DATABASE/…
    - TEXTURES_DIR 覆盖或默认 <站点根>/storage/textures
    """
    site_root = env_path.resolve().parent
    connection = (php_env.get("DB_CONNECTION") or "mysql").strip().lower()
    if connection == "sqlite":
        db_path = php_env.get("DB_DATABASE", "")
        if db_path and not Path(db_path).is_absolute():
            db_path = str(site_root / db_path)
        source = {"scheme": "sqlite", "path": db_path}
    else:
        source = {
            "scheme": "mariadb" if connection == "mariadb" else connection,
            "HOST": php_env.get("DB_HOST") or "127.0.0.1",
            "PORT": php_env.get("DB_PORT") or ("3306" if connection != "pgsql" else "5432"),
            "NAME": php_env.get("DB_DATABASE") or "blessing",
            "USER": php_env.get("DB_USERNAME") or "",
            "PASSWORD": php_env.get("DB_PASSWORD") or "",
        }

    textures = php_env.get("TEXTURES_DIR") or ""
    if textures:
        textures_dir = Path(textures)
        if not textures_dir.is_absolute():
            textures_dir = site_root / textures_dir
    else:
        textures_dir = site_root / "storage" / "textures"
    return source, textures_dir, site_root


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
    def build_django_config(self) -> dict:
        """构造 Django DATABASES 配置并补全 ConnectionHandler 的默认键。

        真实服务器踩坑（KeyError: 'TIME_ZONE'）：运行期注入动态
        alias 时连接管理器已初始化（configure_settings 只在进程
        启动时对 settings.DATABASES 跑一次），裸 conf 不会被补全，
        DatabaseWrapper.__init__ 读取 TIME_ZONE 等键即炸。
        键清单照抄 django.db.utils.ConnectionHandler.configure_settings
        （Django 5.2 源码核对）。
        """
        scheme = self.source["scheme"]
        engine = (
            "django.db.backends.mysql"
            if scheme in ("mysql", "mariadb")
            else "django.db.backends.postgresql"
        )
        conf = {
            "ENGINE": engine,
            "HOST": self.source["HOST"],
            "PORT": self.source["PORT"],
            "NAME": self.source["NAME"],
            "USER": self.source["USER"],
            "PASSWORD": self.source["PASSWORD"],
        }
        # ---- Django 5.2 configure_settings 默认键补全 ----
        conf.setdefault("ATOMIC_REQUESTS", False)
        conf.setdefault("AUTOCOMMIT", True)
        conf.setdefault("CONN_MAX_AGE", 0)
        conf.setdefault("CONN_HEALTH_CHECKS", False)
        conf.setdefault("OPTIONS", {})
        conf.setdefault("TIME_ZONE", None)
        for key in ("NAME", "USER", "PASSWORD", "HOST", "PORT"):
            conf.setdefault(key, "")
        return conf

    def open(self):
        if self.source["scheme"] == "sqlite":
            self._sqlite = sqlite3.connect(self.source["path"])
        else:
            alias = "php_source"
            conf = self.build_django_config()
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

    # 方言化标识符引用（真实服务器踩坑：MySQL 默认模式双引号是字符串
    # 字面量，SELECT * FROM "users" 语法错误；PostgreSQL 才用双引号）
    # MySQL_SCHEMA_LIMIT / PGSQL_SCHEMA_LIMIT：限定当前库，防 1Panel 等
    # 多库共存环境里其他库的同名表被误判存在
    MYSQL_IDENTIFIER_QUOTE = "`"
    PGSQL_IDENTIFIER_QUOTE = '"'
    _HAS_TABLE_MYSQL = (
        "SELECT COUNT(*) FROM information_schema.tables"
        " WHERE table_name = %s AND table_schema = DATABASE()"
    )
    _HAS_TABLE_PGSQL = (
        "SELECT COUNT(*) FROM information_schema.tables"
        " WHERE table_name = %s AND table_schema = current_schema()"
    )

    def quote_table(self, table: str) -> str:
        """按 scheme 生成正确的标识符引用。"""
        if self._sqlite is not None:
            return table  # sqlite 表名来自白名单+管理员前缀，裸引用
        if self.source["scheme"] in ("mysql", "mariadb"):
            return f"`{table}`"  # MySQL/MariaDB 默认模式必须反引号
        return f'"{table}"'  # PostgreSQL ANSI 双引号

    def rows(self, table: str) -> list[dict]:
        if self._sqlite is not None:
            return self._fetch_all(self._cursor(), f"SELECT * FROM {table}", [])
        return self._fetch_all(
            self._cursor(), f"SELECT * FROM {self.quote_table(table)}", []
        )

    def has_table(self, table: str) -> bool:
        cursor = self._cursor()
        if self._sqlite is not None:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            )
            return bool(cursor.fetchone())
        if self.source["scheme"] in ("mysql", "mariadb"):
            cursor.execute(self._HAS_TABLE_MYSQL, [table])
        else:
            cursor.execute(self._HAS_TABLE_PGSQL, [table])
        return bool(cursor.fetchone()[0])


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
    help = (
        "从 PHP 原版（blessing-skin-server）迁移到本系统。\n"
        "推荐：--env <PHP站/.env> 自动读取 DB 连接/盐/皮肤目录，"
        "并将皮肤文件物理复制进新站存储；\n"
        "兼容：--source <DSN> 仅迁移数据库（不复制文件）"
    )

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--env",
            help="PHP 站点 .env 文件路径（完整迁移：数据库 + 皮肤文件复制）",
        )
        group.add_argument("--source", help="旧库 DSN（仅数据库迁移）")
        parser.add_argument(
            "--table-prefix",
            default="",
            help="旧库表前缀（原版 DB_PREFIX，默认无前缀）",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只检查不写入（连接/统计/完整性预检报告）",
        )

    def handle(self, *args, **options):
        # ---- --env 模式：解析 PHP .env，组装 source 与文件复制计划 ----
        textures_dir = None
        if options.get("env"):
            env_path = Path(options["env"])
            if not env_path.is_file():
                raise CommandError(f".env 文件不存在：{env_path}")
            php_env = parse_php_env_file(env_path)
            source, textures_dir, site_root = build_dsn_from_env(php_env, env_path)
            # SALTED2* 历史用户登录依赖全局盐（写入进程环境）
            pwd_method = (php_env.get("PWD_METHOD") or "").strip().upper()
            if php_env.get("SALT"):
                os.environ["SALT"] = php_env["SALT"]
                self.stdout.write(
                    f"已捕获 SALT（{len(php_env['SALT'])} 字符），"
                    "SALTED2* 历史用户登录可用"
                )
            elif pwd_method.startswith("SALTED2"):
                self.stdout.write(self.style.ERROR(
                    f"警告：PWD_METHOD={pwd_method} 但 .env 未配置 SALT！"
                    "SALTED2* 算法的用户密码 = hash(hash(密码)+盐)，"
                    "缺盐则历史用户全部无法登录。请从旧站 .env 补全 SALT 后重跑。"
                ))
            if pwd_method:
                self.stdout.write(
                    f"旧站密码算法：{pwd_method}"
                    "（Django 版按散列特征自动识别全部 10 种算法，"
                    "登录成功后自动升级 Argon2）"
                )
            # 显式交代两个「读取但不迁移」的键，消除迁移者困惑：
            if php_env.get("APP_KEY"):
                self.stdout.write(
                    "APP_KEY：已忽略 —— Laravel session 不跨站迁移；"
                    "密码重置链接在本站为一次性数据库令牌，无需 Laravel 签名密钥"
                )
            if php_env.get("JWT_SECRET"):
                self.stdout.write(
                    "JWT_SECRET：已忽略 —— 旧站 Yggdrasil 令牌存于其缓存"
                    "（非数据库）且 3-7 天有效期，无可搬数据；"
                    "玩家启动器重新登录即签发新令牌"
                )
            title = php_env.get("APP_NAME") or php_env.get("SITE_NAME") or ""
            if title:
                self.stdout.write(f"旧站名称：{title}")
            self.stdout.write(f"旧站皮肤目录：{textures_dir}")
            _ = site_root
        else:
            source = parse_source(options["source"])

        prefix = options["table_prefix"]

        legacy = LegacyDatabase(source)
        try:
            legacy.open()
        except Exception as exc:  # noqa: BLE001 - 连接错误统一转译
            raise CommandError(f"无法连接旧库：{exc}") from exc

        def table(name: str) -> str:
            return prefix + name

        self.stdout.write(f"已连接旧库（{legacy.source['scheme']}）")

        dry_run = bool(options.get("dry_run"))

        stats = {}
        dry_hashes = []  # dry-run 时从旧库直接取（新库未写入）

        def safe_rows(tbl: str) -> list:
            """缺表（老站早期版本）返回空列表而非中断。"""
            if not legacy.has_table(prefix + tbl):
                self.stdout.write(f"（旧库无 {tbl} 表，跳过）")
                return []
            return legacy.rows(prefix + tbl)

        with transaction.atomic():
            if dry_run:
                for name, tbl in (
                    ("users", "users"),
                    ("players", "players"),
                    ("textures", "textures"),
                    ("closet", "user_closet"),
                    ("reports", "reports"),
                    ("options", "options"),
                ):
                    rows = safe_rows(tbl)
                    stats[name] = len(rows)
                    if name == "textures":
                        dry_hashes = [r["hash"] for r in rows]
                if legacy.has_table(prefix + "notifications"):
                    stats["notifications"] = len(legacy.rows(prefix + "notifications"))
                transaction.set_rollback(True)  # 双保险：预检绝不落库
            else:
                # ---- 依赖序（真实 InnoDB 即时 FK 检查下唯一可行顺序）----
                # users → textures → players → closet → reports → notifications
                self.migration_warnings = []
                stats["users"] = self.migrate_users(safe_rows("users"))
                valid_uids = set(User.objects.values_list("uid", flat=True))

                stats["textures"] = self.migrate_textures(
                    safe_rows("textures"), valid_uids
                )
                valid_tids = set(Texture.objects.values_list("tid", flat=True))

                stats["players"] = self.migrate_players(
                    legacy, prefix, valid_uids, valid_tids
                )
                stats["closet"] = self.migrate_closet(
                    safe_rows("user_closet"), valid_uids, valid_tids
                )
                stats["reports"] = self.migrate_reports(
                    safe_rows("reports"), valid_uids, valid_tids
                )
                stats["options"] = self.migrate_options(safe_rows("options"))
                if legacy.has_table(prefix + "notifications"):
                    stats["notifications"] = self.migrate_notifications(
                        legacy.rows(prefix + "notifications"), valid_uids
                    )
                else:
                    self.stdout.write("（旧库无 notifications 表，跳过站内通知）")
                if self.migration_warnings:
                    self.stdout.write(self.style.WARNING("---- 引用完整性告警 ----"))
                    for w in self.migration_warnings:
                        self.stdout.write(self.style.WARNING(f"  {w}"))

        legacy.close()

        # ---- 皮肤文件迁移（文件系统操作不受 DB 事务保护，置于事务之外）----
        if textures_dir is not None:
            if dry_run:
                file_stats = self.check_texture_files(dry_hashes, textures_dir)
            else:
                hashes = list(Texture.objects.values_list("hash", flat=True))
                file_stats = self.copy_texture_files(hashes, textures_dir)
        else:
            file_stats = None

        self.print_report(stats, file_stats=file_stats, dry_run=dry_run)
        if dry_run:
            self.stdout.write(self.style.WARNING("[dry-run] 未写入任何数据、未复制任何文件"))
        else:
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

    def warn(self, message: str) -> None:
        """收集引用完整性告警（尾部统一输出，避免刷屏中断）。"""
        self.migration_warnings.append(message)

    def migrate_players(self, legacy, prefix: str, valid_uids: set, valid_tids: set) -> int:
        uuid_map = {}
        if legacy.has_table(prefix + "uuid"):
            for row in legacy.rows(prefix + "uuid"):
                uuid_map[row["name"]] = row["uuid"]

        from players.uuidgen import generate_uuid_v4

        players_rows = legacy.rows(prefix + "players") if legacy.has_table(
            prefix + "players"
        ) else []
        migrated = 0
        for row in players_rows:
            # 列名两代兼容（真实服务器踩坑第三例）：
            # <2019_03_13 为 player_name，之后 rename 为 name（BS 6.x）
            name = row.get("name") or row.get("player_name")
            pid = row.get("pid")
            if not name:
                self.warn(f"跳过 players pid={pid}：缺少角色名列")
                continue
            if row.get("uid") not in valid_uids:
                self.warn(f"跳过 players pid={pid}（{name}）：uid={row.get('uid')} 无对应用户")
                continue
            # 旧站无 FK 约束，材质引用可能悬空：保留角色、断链置空并告警
            skin = row.get("tid_skin") or 0
            cape = row.get("tid_cape") or 0
            if skin and skin not in valid_tids:
                self.warn(f"players pid={pid}（{name}）：tid_skin={skin} 无对应纹理，置空")
                skin = None
            if cape and cape not in valid_tids:
                self.warn(f"players pid={pid}（{name}）：tid_cape={cape} 无对应纹理，置空")
                cape = None
            Player.objects.update_or_create(
                pid=pid,
                defaults={
                    "uid_id": row["uid"],
                    "name": name,
                    "uuid": uuid_map.get(name) or generate_uuid_v4(),
                    "tid_skin_id": skin or None,
                    "tid_cape_id": cape or None,
                    "last_modified": _dt(row.get("last_modified")),
                },
            )
            migrated += 1
        return migrated

    def migrate_textures(self, source_rows, valid_uids: set) -> int:
        migrated = 0
        for row in source_rows:
            tid = row.get("tid")
            if row.get("uploader") not in valid_uids:
                self.warn(
                    f"跳过 textures tid={tid}（{row.get('name')}）："
                    f"uploader={row.get('uploader')} 无对应用户"
                )
                continue
            Texture.objects.update_or_create(
                tid=tid,
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
            migrated += 1
        return migrated

    def migrate_closet(self, source_rows, valid_uids: set, valid_tids: set) -> int:
        migrated = 0
        for row in source_rows:
            owner = row.get("user_uid")
            tid = row.get("texture_tid")
            if owner not in valid_uids or tid not in valid_tids:
                self.warn(
                    f"跳过 user_closet（uid={owner}, tid={tid}）：引用悬空"
                )
                continue
            ClosetItem.objects.update_or_create(
                owner_id=owner,
                texture_id=tid,
                defaults={"item_name": row.get("item_name") or ""},
            )
            migrated += 1
        return migrated

    def migrate_reports(self, source_rows, valid_uids: set, valid_tids: set) -> int:
        migrated = 0
        for row in source_rows:
            rid = row.get("id")
            if (
                row.get("tid") not in valid_tids
                or row.get("uploader") not in valid_uids
                or row.get("reporter") not in valid_uids
            ):
                self.warn(f"跳过 reports id={rid}：引用悬空")
                continue
            Report.objects.update_or_create(
                id=rid,
                defaults={
                    "texture_id": row["tid"],
                    "uploader_id": row["uploader"],
                    "reporter_id": row["reporter"],
                    "reason": row["reason"],
                    "status": row.get("status") or 0,
                    "report_at": _dt(row.get("report_at")),
                },
            )
            migrated += 1
        return migrated

    def migrate_options(self, source_rows) -> int:
        count = 0
        for row in source_rows:
            value = row.get("option_value")
            if value is not None:
                Option.objects.set(row["option_name"], value)
                count += 1
        return count

    def migrate_notifications(self, source_rows, valid_uids: set) -> int:
        """Laravel notifications：notifiable_id = 收件用户，data JSON 拆 title。

        update_or_create 键：(recipient, title, read_at) —— 历史数据去重语义，
        重跑不重复。created_at 为 auto_now_add，无法保留旧时间戳（可接受）。
        """
        migrated = 0
        for row in source_rows:
            recipient = row.get("notifiable_id")
            if recipient not in valid_uids:
                self.warn(f"跳过 notification（收件人 uid={recipient} 无对应用户）")
                continue
            try:
                data = json.loads(row.get("data") or "{}")
            except json.JSONDecodeError:
                data = {}
            title = data.get("title") or data.get("subject") or row.get("type") or "通知"
            Notification.objects.update_or_create(
                recipient_id=recipient,
                title=title,
                read_at=_dt(row.get("read_at")),
                defaults={
                    "content": data.get("body") or data.get("content") or "",
                },
            )
            migrated += 1
        return migrated

    def print_report(self, stats: dict, file_stats: dict | None = None, dry_run: bool = False):
        mode = "[dry-run] " if dry_run else ""
        self.stdout.write(f"---- {mode}行数报告 ----")
        # 新库计数仅在真实迁移后有意义；dry-run 严禁触新库
        #（真实场景：服务器未建表先跑预检）
        new_counts = {}
        if not dry_run:
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

        if file_stats:
            self.stdout.write(f"---- {mode}皮肤文件报告 ----")
            if "checked" in file_stats:  # dry-run 预检形态
                self.stdout.write(
                    f"  需迁移 {file_stats['checked']}："
                    f"源目录存在 {file_stats['present']}，"
                    f"缺失 {len(file_stats['missing'])}"
                )
            else:
                self.stdout.write(
                    f"  复制 {file_stats['copied']}，已存在跳过 {file_stats['skipped_same']}，"
                    f"缺失 {len(file_stats['missing'])}"
                )
            for digest in file_stats["missing"][:10]:
                self.stdout.write(self.style.WARNING(f"    缺失: {digest}"))
            if len(file_stats["missing"]) > 10:
                self.stdout.write(
                    self.style.WARNING(f"    … 及其余 {len(file_stats['missing']) - 10} 个")
                )
            for warning in file_stats["warnings"]:
                self.stdout.write(self.style.WARNING(f"    告警: {warning}"))

    # ---- 皮肤文件迁移 ----

    def copy_texture_files(self, hashes: list, textures_dir: Path) -> dict:
        """把旧站皮肤文件【物理复制】到新站 TEXTURE_STORAGE_ROOT。

        - 目标已存在且内容一致 → 跳过（重跑幂等）
        - 目标已存在但内容不一致 → 报告告警，不覆盖（保护新站数据）
        - 源缺失 → 报告（DB 记录保留，引用不断链）
        - 复制后校验 sha256 与文件名一致（损坏数据告警但保留现状）
        """
        from textures.storage import texture_storage

        copied, skipped_same, warnings, missing = 0, 0, [], []
        for digest in hashes:
            source_file = textures_dir / digest
            if not source_file.is_file():
                missing.append(digest)
                continue
            if not texture_storage.exists(digest):
                data = source_file.read_bytes()
                texture_storage.save_bytes(digest, data)
                copied += 1
                if hashlib.sha256(data).hexdigest() != digest:
                    warnings.append(f"{digest[:12]}… 内容与文件名 hash 不符（已按现状复制）")
            else:
                existing = texture_storage.read(digest)
                if existing == source_file.read_bytes():
                    skipped_same += 1
                else:
                    warnings.append(
                        f"{digest[:12]}… 新站已有同名但内容不同的文件，未覆盖"
                    )

        return {
            "copied": copied,
            "skipped_same": skipped_same,
            "missing": missing,
            "warnings": warnings,
        }

    def check_texture_files(self, hashes: list, textures_dir: Path) -> dict:
        """dry-run 预检：只统计，不复制。"""
        present, missing = 0, []
        for digest in hashes:
            if (textures_dir / digest).is_file():
                present += 1
            else:
                missing.append(digest)
        return {
            "present": present,
            "missing": missing,
            "checked": len(hashes),
            "warnings": [],
        }
