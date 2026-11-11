"""测试配置：内存 SQLite + 固定 SECRET_KEY（签名值跨用例可复现）。"""

from .base import *  # noqa: F401,F403

SECRET_KEY = "test-only-fixed-secret-key"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}

# 邮件进内存 outbox，测试可断言
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
