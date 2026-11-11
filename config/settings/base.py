"""Django 基础配置。

设计对照 PHP 原版（Laravel）：
- 环境变量命名沿用原版 .env（DB_CONNECTION / DB_HOST / DB_*），
  PHP 部署上的旧 .env 可直接复用，降低迁移成本；
- .env 文件自动加载，真实环境变量优先（与 Laravel dotenv 同语义）；
  加载位置可经 ENV_FILE 环境变量重定向；
- 生产模式（DEBUG=False）缺少 SECRET_KEY 直接拒绝启动，
  等价于 Laravel 缺少 APP_KEY 时的报错；
- 默认数据库 SQLite（本地开发与测试约定），MySQL / PgSQL 按需切换。

原版 DB_PREFIX（表前缀）不再支持：新 schema 全新设计，
旧表前缀由迁移脚本在搬运时剥除。
"""

import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# .env 自动加载；真实环境变量优先（override=False）
ENV_FILE = Path(os.environ.get("ENV_FILE", str(BASE_DIR / ".env")))
load_dotenv(ENV_FILE, override=False)


def env_bool(name: str, default: bool) -> bool:
    """解析布尔环境变量，兼容 1/true/yes/on 与 0/false/no/off。"""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DEBUG = env_bool("DEBUG", True)

# SECRET_KEY：环境变量优先；开发模式缺失时生成进程内临时密钥（重启即失效）
_secret_key = os.environ.get("SECRET_KEY", "")
if not _secret_key:
    if DEBUG:
        _secret_key = secrets.token_urlsafe(48)
    else:
        raise ImproperlyConfigured("生产环境（DEBUG=false）必须设置 SECRET_KEY 环境变量")
SECRET_KEY = _secret_key

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]


def _databases() -> dict:
    """按 DB_CONNECTION（sqlite/mysql/pgsql）构建数据库配置。"""
    connection = os.environ.get("DB_CONNECTION", "sqlite").strip().lower()
    match connection:
        case "sqlite":
            name = os.environ.get("DB_DATABASE", "")
            return {
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": Path(name) if name else BASE_DIR / "db.sqlite3",
                }
            }
        case "mysql":
            return {
                "default": {
                    "ENGINE": "django.db.backends.mysql",
                    "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
                    "PORT": os.environ.get("DB_PORT", "3306"),
                    "NAME": os.environ.get("DB_DATABASE", "blessing"),
                    "USER": os.environ.get("DB_USERNAME", "root"),
                    "PASSWORD": os.environ.get("DB_PASSWORD", ""),
                }
            }
        case "pgsql":
            return {
                "default": {
                    "ENGINE": "django.db.backends.postgresql",
                    "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
                    "PORT": os.environ.get("DB_PORT", "5432"),
                    "NAME": os.environ.get("DB_DATABASE", "blessing"),
                    "USER": os.environ.get("DB_USERNAME", "postgres"),
                    "PASSWORD": os.environ.get("DB_PASSWORD", ""),
                }
            }
        case _:
            raise ImproperlyConfigured(
                f"不支持的 DB_CONNECTION：{connection}（可选 sqlite / mysql / pgsql）"
            )


DATABASES = _databases()

# 旧库迁移专用 alias（migrate_from_php 命令运行时覆盖为真实 DSN）
DATABASES["php_source"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": ":memory:",
}

# 自定义用户模型（必须在首个迁移生成前指定）
AUTH_USER_MODEL = "accounts.User"

# 认证后端：email/角色名登录 + legacy 密码桥接
AUTHENTICATION_BACKENDS = ["accounts.backends.EmailOrPlayerNameBackend"]

# 密码校验链：新密码 Argon2 签发；历史 PHP 散列（bcrypt $2y$/无格式 MD5/SHA 系）兼容校验
PASSWORD_HASHERS = [
    "accounts.hashers.PhpCompatPasswordHasher",
    "django.contrib.auth.hashers.BCryptPasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# ---- OAuth2（DOT，对照原版 Laravel Passport 语义）----
OAUTH2_PROVIDER = {
    # 原版 Passport scope 模型（插件 + 核心默认）
    "SCOPES": {
        "User.Read": "读取用户信息",
        "Player.ReadWrite": "管理角色",
        "PlayerProfile.Read": "读取角色档案",
        "Closet.Read": "读取衣柜",
        "Closet.ReadWrite": "管理衣柜",
        "Notification.Read": "读取通知",
    },
    "DEFAULT_SCOPES": ["User.Read"],
    "ACCESS_TOKEN_EXPIRE_SECONDS": 3600 * 24 * 7,  # 与原版 token 有效期一致
    "REQUEST_APPROVAL_PROMPT": "auto",
    # password grant 已验证可用（Phase 0），保持默认启用
    "APPLICATION_MODEL": "oauth2_provider.Application",
    "PKCE_REQUIRED": False,  # 原 Passport 无 PKCE 要求
}

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # ---- 项目包（管理命令等基础设施）----
    "config",
    # ---- 业务应用（Phase 1 起）----
    "options",
    "accounts",
    "players",
    "textures",
    "yggdrasil",
    "moderation",
    "notifications",
    "invitation",
    "oauth_bridge",
    "setup",
    # ---- 第三方 ----
    "oauth2_provider",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---- 邮件 ----
# SMTP 参数（host/port/用户名等）存 Option 表，管理后台配置（Phase 6 表单），
# 运行时按 option 动态构建 EmailBackend（见 accounts/mailer.py）。
# settings 仅保留发件人默认值；未配置 SMTP 时用控制台后端（开发环境）。
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "noreply@blessing-skin.local"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# 容器内由 whitenoise 托管静态文件（生产可不用 Nginx）
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
