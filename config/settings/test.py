"""测试配置：内存 SQLite + 固定 SECRET_KEY（签名值跨用例可复现）。"""

from .base import *  # noqa: F401,F403

SECRET_KEY = "test-only-fixed-secret-key-0123456789abcdef-32b"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}

# 邮件进内存 outbox，测试可断言
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Manifest storage 需预先 collectstatic（测试无产物），回退默认
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Yggdrasil 节流默认关闭（限流专项测试用 override_settings 打开）
YGG_RATE_LIMIT_SECONDS = 0
YGG_MAX_BODY_BYTES = 1024 * 1024
