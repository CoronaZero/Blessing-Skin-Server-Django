"""测试配置：内存 SQLite + 固定 SECRET_KEY（签名值跨用例可复现）。"""

from .base import *  # noqa: F401,F403

SECRET_KEY = "test-only-fixed-secret-key"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
    # 迁移命令测试用的旧库 alias（migrate_from_php 运行时覆盖 DSN）
    "php_source": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}
