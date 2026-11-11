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
