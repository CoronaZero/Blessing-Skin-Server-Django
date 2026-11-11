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

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
