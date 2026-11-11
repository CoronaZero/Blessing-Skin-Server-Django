"""migrate_from_php 端到端测试（TDD 先行）。

策略：用原生 sqlite3 在临时文件里构造 PHP 原版 schema 的“旧库”
（含样例数据），对 Django 命令传入 --source DSN，断言新库内容
逐项正确。可重跑性与行数比对报告一并覆盖。
"""

import sqlite3

import pytest
from django.core.management import call_command

from accounts.models import User
from moderation.models import Report
from notifications.models import Notification
from options.models import Option
from players.models import Player
from textures.models import ClosetItem, Texture

pytestmark = pytest.mark.django_db  # 旧库走标准库直连，不占 Django 多库标记

# ---- PHP 原版 schema DDL（对照 blessing-skin-server-php database/migrations）----
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
    CREATE TABLE user_closet (
        user_uid INTEGER NOT NULL,
        texture_tid INTEGER NOT NULL,
        item_name TEXT
    )
    """,
    """
    CREATE TABLE reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tid INTEGER NOT NULL,
        uploader INTEGER NOT NULL,
        reporter INTEGER NOT NULL,
        reason LONGTEXT NOT NULL,
        status INTEGER DEFAULT 0,
        report_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        option_name VARCHAR(50) NOT NULL,
        option_value LONGTEXT
    )
    """,
    # yggdrasil-api 插件表（存在时迁移 uuid 映射）
    """
    CREATE TABLE uuid (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR NOT NULL,
        uuid VARCHAR(255) NOT NULL
    )
    """,
    # notification 插件表（Laravel notifications）
    """
    CREATE TABLE notifications (
        id CHAR(36) PRIMARY KEY,
        type VARCHAR NOT NULL,
        notifiable_type VARCHAR NOT NULL,
        notifiable_id INTEGER NOT NULL,
        data TEXT NOT NULL,
        read_at TIMESTAMP NULL,
        created_at TIMESTAMP NULL,
        updated_at TIMESTAMP NULL
    )
    """,
]


@pytest.fixture()
def php_db(tmp_path):
    """构造填充样例数据的 PHP 版旧库，返回 (dsn, 数据快照)。"""
    db_file = tmp_path / "php_blessing.sqlite3"
    conn = sqlite3.connect(db_file)
    for ddl in PHP_DDL:
        conn.execute(ddl)

    conn.execute(
        "INSERT INTO users (uid, email, nickname, score, avatar, password, ip,"
        " permission, last_sign_at, register_at, verified) VALUES"
        " (1, 'admin@x.com', '管理员', 100, 0, ?,"
        " '127.0.0.1', 2, '2020-01-01 00:00:00', '2019-01-01 00:00:00', 1)",
        ["$2y$10$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ012345"],
    )
    conn.execute(
        "INSERT INTO users (uid, email, nickname, score, avatar, password, ip,"
        " permission, last_sign_at, register_at, verified)"
        " VALUES (2, 'user@x.com', '普通用户', 10, 0, 'legacymd5hash',"
        " '192.0.2.1', 0, '2020-02-01 00:00:00', '2020-01-15 00:00:00', 0)"
    )
    conn.execute(
        "INSERT INTO players (pid, uid, player_name, tid_skin, tid_cape, last_modified)"
        " VALUES (1, 2, 'Steve', 1, 0, '2020-05-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO textures (tid, name, type, hash, size, uploader, public,"
        " upload_at, likes)"
        " VALUES (1, '默认皮肤', 'steve', ?, 3, 2, 1, '2020-03-01 00:00:00', 5)",
        ["aabbccdd" * 8],
    )
    conn.execute(
        "INSERT INTO user_closet (user_uid, texture_tid, item_name)"
        " VALUES (2, 1, '收藏的皮肤')"
    )
    conn.execute(
        "INSERT INTO reports (tid, uploader, reporter, reason, status, report_at)"
        " VALUES (1, 2, 1, '违规', 0, '2020-06-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO options (option_name, option_value)"
        " VALUES ('site_name', '我的老皮肤站')"
    )
    conn.execute(
        "INSERT INTO options (option_name, option_value)"
        " VALUES ('regs_per_ip', '4')"
    )
    conn.execute(
        "INSERT INTO uuid (name, uuid) VALUES ('Steve', ?)",
        ["deadbeef" * 4],
    )
    conn.execute(
        "INSERT INTO notifications (id, type, notifiable_type, notifiable_id,"
        " data, read_at, created_at) VALUES"
        " ('n-uuid-1', 'App\\\\Notifications\\\\TestNotification',"
        " 'App\\\\Models\\\\User', 2, '{\"title\":\"旧通知\",\"body\":\"你好\"}',"
        " NULL, '2020-07-01 00:00:00')"
    )
    conn.commit()
    conn.close()
    return f"sqlite:///{db_file}", db_file


def run_migration(dsn, capture=None):
    """调用命令（直接进程隔离会更好，这里用 call_command 简化）。"""
    from io import StringIO

    out = StringIO()
    call_command("migrate_from_php", source=dsn, stdout=out)
    return out.getvalue()


class TestUserMigration:
    def test_users_row_count_and_fields(self, php_db, db):
        dsn, _ = php_db
        run_migration(dsn)
        assert User.objects.count() == 2
        admin = User.objects.get(email="admin@x.com")
        assert admin.nickname == "管理员"
        assert admin.permission == User.Permission.SUPER_ADMIN
        assert admin.verified is True

    def test_password_moved_verbatim(self, php_db, db):
        """密码散列原样搬运（登录时由 hasher 兼容层校验/升级）。"""
        dsn, _ = php_db
        run_migration(dsn)
        user = User.objects.get(email="user@x.com")
        assert user.password == "legacymd5hash"

    def test_timestamps_preserved(self, php_db, db):
        dsn, _ = php_db
        run_migration(dsn)
        user = User.objects.get(email="user@x.com")
        assert user.register_at.year == 2020
        assert user.register_at.month == 1


class TestPlayerMigration:
    def test_player_with_uuid(self, php_db, db):
        dsn, _ = php_db
        run_migration(dsn)
        player = Player.objects.get(name="Steve")
        # uuid 表并入 player
        assert player.uuid == "deadbeef" * 4
        assert player.uid.email == "user@x.com"

    def test_player_without_uuid_entry_gets_new(self, php_db, db):
        """uuid 表缺记录时自动分配（v4 默认算法）。"""
        dsn, db_file = php_db
        conn = sqlite3.connect(db_file)
        conn.execute("INSERT INTO players (uid, player_name, tid_skin, tid_cape, last_modified)"
                     " VALUES (2, 'Alex', 0, 0, '2020-05-01 00:00:00')")
        conn.commit()
        conn.close()
        run_migration(dsn)
        player = Player.objects.get(name="Alex")
        assert len(player.uuid) == 32


class TestTextureMigration:
    def test_texture_fields(self, php_db, db):
        dsn, _ = php_db
        run_migration(dsn)
        t = Texture.objects.get(hash="aabbccdd" * 8)
        assert t.name == "默认皮肤"
        assert t.type == "steve"
        assert t.size == 3
        assert t.public is True
        assert t.uploader.email == "user@x.com"

    def test_report_migrated(self, php_db, db):
        dsn, _ = php_db
        run_migration(dsn)
        r = Report.objects.first()
        assert r.reason == "违规"
        assert r.status == Report.Status.PENDING
        assert r.informer.email == "admin@x.com"

    def test_closet_migrated(self, php_db, db):
        dsn, _ = php_db
        run_migration(dsn)
        item = ClosetItem.objects.get()
        assert item.item_name == "收藏的皮肤"
        assert item.owner.email == "user@x.com"


class TestOptionsMigration:
    def test_values_copied(self, php_db, db):
        dsn, _ = php_db
        run_migration(dsn)
        assert Option.objects.get_value("site_name") == "我的老皮肤站"
        assert Option.objects.get_value("regs_per_ip") == "4"


class TestNotificationMigration:
    def test_pending_notification_migrated(self, php_db, db):
        """未读通知搬运（data JSON 中 title 提取，其余归入 content）。"""
        dsn, _ = php_db
        run_migration(dsn)
        n = Notification.objects.first()
        recipient = User.objects.get(email="user@x.com")
        assert n.recipient == recipient
        assert n.title == "旧通知"
        assert n.read_at is None


class TestIdempotentAndReport:
    def test_rerun_is_safe(self, php_db, db):
        """可重跑：第二次运行不清空、不重复（按业务键 upsert）。"""
        dsn, _ = php_db
        run_migration(dsn)
        run_migration(dsn)  # 重跑
        assert User.objects.count() == 2
        assert Texture.objects.count() == 1
        assert Report.objects.count() == 1

    def test_report_output(self, php_db, db):
        dsn, _ = php_db
        output = run_migration(dsn)
        # 行数比对报告包含各表计数
        assert "users" in output
        assert "players" in output


class TestMissingTables:
    def test_source_without_ygg_tables(self, tmp_path, db):
        """无 uuid/notifications 表的旧库（未装插件）也能迁移。"""
        db_file = tmp_path / "bare.sqlite3"
        conn = sqlite3.connect(db_file)
        for ddl in PHP_DDL[:6]:  # 去掉 uuid/notifications
            conn.execute(ddl)
        conn.execute(
            "INSERT INTO users (uid, email, nickname, score, avatar, password, ip,"
            " permission, last_sign_at, register_at)"
            " VALUES (9, 'solo@x.com', 'solo', 0, 0, 'x', '127.0.0.1', 0,"
            " '2020-01-01 00:00:00', '2020-01-01 00:00:00')"
        )
        conn.commit()
        conn.close()
        run_migration(f"sqlite:///{db_file}")
        assert User.objects.filter(email="solo@x.com").exists()
