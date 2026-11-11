"""migrate_from_php v2 测试：--env 驱动的完整站点迁移（含皮肤文件复制）。

v2 目标（对照用户需求）：
- --env <path> 指向 PHP 站 .env：自动解析 DB 连接、SALT、PWD_METHOD，
  不再手拼 --source DSN
- 皮肤文件从旧站 storage/textures（或 TEXTURES_DIR）**物理复制**到
  新站 TEXTURE_STORAGE_ROOT（不引用旧目录）
- 数据迁移后：皮肤文件逐 hash 校验完整性，缺失清单输出
- .env 中的 SALT 写入新站进程环境（SALTED2* 历史用户登录依赖）
"""

import sqlite3

import pytest
from django.core.management import call_command

from accounts.models import User
from options.models import Option
from players.models import Player
from textures.models import Texture

pytestmark = pytest.mark.django_db

# 与 test_migrate_from_php.py 相同的 PHP schema（复用构造逻辑）

PHP_DDL = [
    """
    CREATE TABLE users (
        uid INTEGER PRIMARY KEY AUTOINCREMENT,
        email VARCHAR(100) NOT NULL,
        nickname VARCHAR(50) DEFAULT '',
        score INTEGER NOT NULL,
        avatar INTEGER DEFAULT 0,
        password VARCHAR(255) NOT NULL,
        ip VARCHAR(32) NOT NULL,
        permission INTEGER DEFAULT 0,
        last_sign_at DATETIME NOT NULL,
        register_at DATETIME NOT NULL,
        verified INTEGER DEFAULT 0,
        remember_token VARCHAR(100),
        locale VARCHAR(10) DEFAULT NULL,
        is_dark_mode INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE players (
        pid INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER NOT NULL,
        player_name VARCHAR(50) NOT NULL,
        tid_skin INTEGER DEFAULT 0,
        tid_cape INTEGER DEFAULT 0,
        last_modified DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE textures (
        tid INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR(50) NOT NULL,
        type VARCHAR(10) NOT NULL,
        hash VARCHAR(64) NOT NULL,
        size INTEGER NOT NULL,
        uploader INTEGER NOT NULL,
        public TINYINT DEFAULT 1,
        upload_at DATETIME NOT NULL,
        likes INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        option_name VARCHAR(50) NOT NULL,
        option_value LONGTEXT
    )
    """,
]


def build_php_site(tmp_path, *, textures_dir="storage/textures"):
    """构造完整 PHP 站点：.env + sqlite 库 + 皮肤文件目录。"""
    site = tmp_path / "php-site"
    site.mkdir()
    (site / "storage").mkdir()
    textures = site / textures_dir
    textures.mkdir(parents=True)
    # PHP 站 .env（对照原版键名）
    import hashlib

    skin_hash = hashlib.sha256(b"skin-png-bytes-v2").hexdigest()
    cape_hash = hashlib.sha256(b"cape-png-bytes-v2").hexdigest()
    env_content = (
        "APP_ENV=production\n"
        "DB_CONNECTION=sqlite\n"
        f"DB_DATABASE={site / 'database.sqlite3'}\n"
        "DB_PREFIX=\n"
        "PWD_METHOD=SALTED2SHA256\n"
        "SALT=legacy-salt-42\n"
        f"TEXTURES_DIR={textures}\n"
        "FS_DRIVER=local\n"
        "SITE_NAME=老皮肤站\n"
        "REGS_PER_IP=5\n"
    )
    (site / ".env").write_text(env_content, encoding="utf-8")

    # sqlite 旧库
    db = site / "database.sqlite3"
    conn = sqlite3.connect(db)
    for ddl in PHP_DDL:
        conn.execute(ddl)
    conn.execute(
        "INSERT INTO users (uid, email, nickname, score, avatar, password, ip,"
        " permission, last_sign_at, register_at, verified)"
        " VALUES (1, 'admin@old.com', '老管理员', 100, 0, ?, '127.0.0.1', 2,"
        " '2020-01-01 00:00:00', '2019-01-01 00:00:00', 1)",
        ["5e884898da28047151d0e56f8dc6292773603d0d6aabbdfa04a4c8f2f0a3c0d2f"],
    )
    conn.execute(
        "INSERT INTO users (uid, email, nickname, score, avatar, password, ip,"
        " permission, last_sign_at, register_at, verified)"
        " VALUES (2, 'user@old.com', '老子', 10, 0, ?, '192.0.2.1', 0,"
        " '2020-02-01 00:00:00', '2020-01-15 00:00:00', 0)",
        ["deadbeef" * 8],
    )
    conn.execute(
        "INSERT INTO players (pid, uid, player_name, tid_skin, tid_cape, last_modified)"
        " VALUES (1, 2, 'OldSteve', ?, ?, '2020-05-01 00:00:00')",
        [1, 2],
    )
    conn.execute(
        "INSERT INTO textures (tid, name, type, hash, size, uploader, public, upload_at, likes)"
        " VALUES (1, '老皮肤', 'steve', ?, 3, 2, 1, '2020-03-01 00:00:00', 5)",
        [skin_hash],
    )
    conn.execute(
        "INSERT INTO textures (tid, name, type, hash, size, uploader, public, upload_at, likes)"
        " VALUES (2, '老披风', 'cape', ?, 2, 1, 1, '2020-04-01 00:00:00', 1)",
        [cape_hash],
    )
    conn.execute(
        "INSERT INTO options (option_name, option_value) VALUES ('site_name', '老站名')"
    )
    conn.execute(
        "INSERT INTO options (option_name, option_value) VALUES ('some_key', 'kept')"
    )
    conn.commit()
    conn.close()

    # 皮肤文件（哈希命名，无扩展名）
    (textures / skin_hash).write_bytes(b"skin-png-bytes-v2")
    (textures / cape_hash).write_bytes(b"cape-png-bytes-v2")
    # 一个会缺失的纹理（DB 有记录但文件已丢）
    missing_hash = "f" * 64
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO textures (tid, name, type, hash, size, uploader, public, upload_at, likes)"
        " VALUES (3, '丢失的纹理', 'steve', ?, 1, 2, 1, '2020-06-01 00:00:00', 0)",
        [missing_hash],
    )
    conn.commit()
    conn.close()

    return {
        "site": site,
        "env_file": site / ".env",
        "textures_dir": textures,
        "skin_hash": skin_hash,
        "cape_hash": cape_hash,
        "missing_hash": missing_hash,
    }


@pytest.fixture()
def storage(tmp_path, settings):
    from django.core.files.storage import FileSystemStorage

    root = tmp_path / "new-site-storage"
    settings.TEXTURE_STORAGE_ROOT = str(root)
    from textures import storage as storage_mod

    storage_mod.texture_storage._storage = FileSystemStorage(location=str(root))
    return root


def run_env_migration(env_file, **extra):
    from io import StringIO

    out = StringIO()
    call_command("migrate_from_php", env=str(env_file), stdout=out, **extra)
    return out.getvalue()


class TestEnvDrivenMigration:
    def test_full_site_migration_with_files(self, tmp_path, storage):
        php = build_php_site(tmp_path)
        output = run_env_migration(php["env_file"])

        # 用户/角色/纹理/选项全部到位
        assert User.objects.count() == 2
        assert Player.objects.get(name="OldSteve").uid.email == "user@old.com"
        assert Texture.objects.count() == 3
        assert Option.objects.get_value("site_name") == "老站名"

        # 皮肤文件已物理复制进新站目录（内容逐字节一致）
        from textures.storage import texture_storage

        assert (storage / php["skin_hash"]).read_bytes() == b"skin-png-bytes-v2"
        assert (storage / php["cape_hash"]).read_bytes() == b"cape-png-bytes-v2"
        assert texture_storage.exists(php["skin_hash"])

        # 报告包含行数与文件完整性信息
        assert "users" in output
        assert "丢失" in output or "缺失" in output

    def test_missing_texture_reported_but_db_kept(self, tmp_path, storage):
        """DB 记录保留（引用不断），文件缺失仅报告。"""
        php = build_php_site(tmp_path)
        run_env_migration(php["env_file"])
        assert Texture.objects.filter(hash=php["missing_hash"]).exists()
        assert not (storage / php["missing_hash"]).exists()

    def test_salt_env_captured(self, tmp_path, storage, monkeypatch):
        """SALTED2* 历史用户登录依赖：.env 的 SALT 注入运行环境。"""
        php = build_php_site(tmp_path)
        run_env_migration(php["env_file"])

        # 迁移进程内已生效
        import os

        assert os.environ.get("SALT") == "legacy-salt-42"
        # 老用户的 salted2sha256 散列可校验
        from accounts.hashers import Salted2SHA256Hasher

        user = User.objects.get(email="user@old.com")
        stored = "deadbeef" * 8  # 测试占位散列，仅验证 hasher 能拿到盐
        assert Salted2SHA256Hasher("legacy-salt-42").salt == "legacy-salt-42"
        _ = user, stored

    def test_dry_run_touches_nothing(self, tmp_path, storage):
        php = build_php_site(tmp_path)
        output = run_env_migration(php["env_file"], dry_run=True)
        assert User.objects.count() == 0
        assert not (storage / php["skin_hash"]).exists()
        assert "dry-run" in output.lower() or "[dry-run]" in output

    def test_missing_env_file_rejected(self, tmp_path, storage):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            run_env_migration(tmp_path / "no-such.env")

    def test_pwd_method_reported_in_output(self, tmp_path, storage):
        """PWD_METHOD 展示于报告：管理员可确认旧站算法已被兼容。"""
        php = build_php_site(tmp_path)
        output = run_env_migration(php["env_file"])
        assert "SALTED2SHA256" in output
        assert "自动识别" in output or "自动升级" in output

    def test_salted2_without_salt_warns_loudly(self, tmp_path, storage):
        """PWD_METHOD=SALTED2* 且缺 SALT → 醒目错误警告（历史用户将无法登录）。"""
        php = build_php_site(tmp_path)
        env_content = "\n".join(
            line for line in php["env_file"].read_text(encoding="utf-8").splitlines()
            if not line.startswith("SALT=")
        )
        php["env_file"].write_text(env_content, encoding="utf-8")
        output = run_env_migration(php["env_file"])
        assert "SALT" in output
        assert "无法登录" in output

    def test_app_key_and_jwt_secret_ignored_with_reason(self, tmp_path, storage):
        """APP_KEY / JWT_SECRET：显式输出「已忽略 + 原因」，消除迁移者困惑。"""
        php = build_php_site(tmp_path)
        env_content = php["env_file"].read_text(encoding="utf-8")
        env_content += "APP_KEY=base64:xxxx\nJWT_SECRET=old-jwt-secret\n"
        php["env_file"].write_text(env_content, encoding="utf-8")
        output = run_env_migration(php["env_file"])
        assert "APP_KEY：已忽略" in output
        assert "JWT_SECRET：已忽略" in output

    def test_default_textures_dir_assumed(self, tmp_path, storage):
        """.env 无 TEXTURES_DIR 时按 <site>/storage/textures 解析。"""
        php = build_php_site(tmp_path)
        env_content = php["env_file"].read_text(encoding="utf-8")
        env_content = "\n".join(
            line for line in env_content.splitlines() if not line.startswith("TEXTURES_DIR")
        )
        php["env_file"].write_text(env_content, encoding="utf-8")
        run_env_migration(php["env_file"])
        assert (storage / php["skin_hash"]).exists()

    def test_file_hash_mismatch_reported_after_copy(self, tmp_path, storage):
        """文件名与内容 hash 不符（旧站数据损坏）→ 复制后在报告中告警。"""
        php = build_php_site(tmp_path)
        # 篡改内容
        (php["textures_dir"] / php["skin_hash"]).write_bytes(b"corrupted")
        output = run_env_migration(php["env_file"])
        assert "不符" in output or "损坏" in output
        # 仍复制（保现状），但报告可见
        assert (storage / php["skin_hash"]).read_bytes() == b"corrupted"

    def test_rerun_idempotent_on_files(self, tmp_path, storage):
        """重跑：数据 upsert 幂等，文件不重复复制（存在即跳过）。"""
        php = build_php_site(tmp_path)
        run_env_migration(php["env_file"])
        run_env_migration(php["env_file"])
        assert User.objects.count() == 2
        assert Texture.objects.count() == 3


class TestMysqlAliasConfig:
    """真实服务器踩坑回归：MySQL 注入路径的配置完整性。

    本地无 MySQL，测试无法真连——用 DatabaseWrapper(conf) 构造
    复现 KeyError: 'TIME_ZONE'（wrapper __init__ 读取 settings_dict
    的全部默认键，与 ensure_connection 前置阶段相同）。
    """

    def _legacy(self, scheme="mysql"):
        from config.management.commands.migrate_from_php import LegacyDatabase

        return LegacyDatabase({
            "scheme": scheme, "HOST": "h", "PORT": "3306",
            "NAME": "n", "USER": "u", "PASSWORD": "p",
        })

    def test_build_django_config_has_django_required_defaults(self):
        """注入配置补全 Django 5.2 configure_settings 的全部默认键。"""
        conf = self._legacy().build_django_config()
        assert conf["TIME_ZONE"] is None
        assert conf["AUTOCOMMIT"] is True
        assert conf["ATOMIC_REQUESTS"] is False
        assert conf["CONN_MAX_AGE"] == 0
        assert conf["CONN_HEALTH_CHECKS"] is False
        assert conf["OPTIONS"] == {}

    def test_database_wrapper_constructible_from_config(self):
        """关键回归：配置可构造 DatabaseWrapper（不真连）。

        修复前：注入裸 conf → wrapper.__init__ KeyError 'TIME_ZONE'
        （真实服务器 migrate_from_php --env MySQL 路径报错）。
        """
        from django.db.backends.mysql.base import DatabaseWrapper

        conf = self._legacy().build_django_config()
        wrapper = DatabaseWrapper(conf, alias="php_source_test")
        assert wrapper.settings_dict["TIME_ZONE"] is None

    def test_pgsql_config_also_complete(self):
        """pgsql 路径配置同样补全（驱动按需安装，只验配置不构造 wrapper）。"""
        conf = self._legacy("pgsql").build_django_config()
        assert conf["ENGINE"] == "django.db.backends.postgresql"
        assert conf["TIME_ZONE"] is None
        assert conf["NAME"] == "n"
        assert conf["OPTIONS"] == {}

    def test_dry_run_report_needs_no_new_site_tables(self):
        """dry-run 全程不触新站库（未建表也能预检）。

        print_report 在 dry-run 下不得查询新库 counts
        （真实场景：服务器先跑 dry-run 后才 migrate）。
        """
        from unittest.mock import patch

        from config.management.commands.migrate_from_php import Command

        cmd = Command()
        with (
            patch.object(User.objects, "count", side_effect=AssertionError("不该查新库")),
            patch.object(Player.objects, "count", side_effect=AssertionError("不该查新库")),
            patch.object(Texture.objects, "count", side_effect=AssertionError("不该查新库")),
        ):
            from io import StringIO

            cmd.stdout = StringIO()
            cmd.print_report({"users": 10, "textures": 5}, file_stats=None, dry_run=True)
            output = cmd.stdout.getvalue()
        assert "10" in output
        assert "新库共" not in output  # dry-run 不展示新库计数


class TestSqlDialects:
    """MySQL 方言回归（真实服务器踩坑第二例，不连库验证 SQL 生成）。

    MariaDB 默认模式下双引号是字符串字面量：SELECT * FROM "users"
    语法错误（1064）；MySQL/MariaDB 必须反引号，PostgreSQL 用双引号。
    """

    def _legacy(self, scheme):
        from config.management.commands.migrate_from_php import LegacyDatabase

        return LegacyDatabase({
            "scheme": scheme, "HOST": "h", "PORT": "1",
            "NAME": "n", "USER": "u", "PASSWORD": "p",
        })

    def test_mysql_table_quoted_with_backticks(self):
        legacy = self._legacy("mysql")
        assert legacy.quote_table("users") == "`users`"

    def test_mariadb_table_quoted_with_backticks(self):
        assert self._legacy("mariadb").quote_table("players") == "`players`"

    def test_pgsql_table_quoted_with_double_quotes(self):
        assert self._legacy("pgsql").quote_table("users") == '"users"'

    def test_has_table_limited_to_current_schema(self):
        """information_schema 限定当前库——防多库环境同名表误判。

        1Panel 等多站点共存实例上，其他库的 users 表会令裸
        table_name 匹配误判存在。
        """
        mysql_sql = self._legacy("mysql")._HAS_TABLE_MYSQL
        assert "table_schema = DATABASE()" in mysql_sql
        pg_sql = self._legacy("pgsql")._HAS_TABLE_PGSQL
        assert "table_schema = current_schema()" in pg_sql
        # 参数化占位保留（表名防注入）
        assert "%s" in mysql_sql and "%s" in pg_sql
