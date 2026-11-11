"""settings 模块契约测试（TDD 先行）。

对照 PHP 原版定义配置契约：
- 环境变量命名沿用原版 .env（DB_CONNECTION/DB_HOST/...），
  便于从 PHP 部署迁移时直接复用旧 .env；
- .env 文件加载且真实环境变量优先（等价于 Laravel dotenv 语义）；
- 生产模式（DEBUG=False）缺少 SECRET_KEY 必须拒绝启动；
- 默认数据库为 SQLite（本地开发约定）。
"""

import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured

# base 模块读取的全部环境变量（测试前统一清空保证每次 reload 结果只取决于用例注入）
MANAGED_ENV_VARS = [
    "ENV_FILE",
    "DEBUG",
    "SECRET_KEY",
    "ALLOWED_HOSTS",
    "DB_CONNECTION",
    "DB_HOST",
    "DB_PORT",
    "DB_DATABASE",
    "DB_USERNAME",
    "DB_PASSWORD",
]


def load_base(monkeypatch, tmp_path, **env):
    """清空相关环境变量后按需注入，再重新加载 base 模块。

    base 在 import 时读取环境变量，因此用 importlib.reload 驱动；
    ENV_FILE 默认指向不存在的临时路径，屏蔽开发者本机 .env 的干扰。
    特殊参数 __env_file 用于测试 .env 加载本身。
    """
    env_file = env.pop("__env_file", None)
    for key in MANAGED_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(
        "ENV_FILE", str(env_file) if env_file else str(tmp_path / "no-such.env")
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    import config.settings.base as base

    return importlib.reload(base)


class TestDatabase:
    def test_default_is_sqlite(self, monkeypatch, tmp_path):
        base = load_base(monkeypatch, tmp_path)
        db = base.DATABASES["default"]
        assert db["ENGINE"] == "django.db.backends.sqlite3"
        assert str(db["NAME"]).endswith("db.sqlite3")

    def test_sqlite_name_overridable_via_db_database(self, monkeypatch, tmp_path):
        base = load_base(monkeypatch, tmp_path, DB_DATABASE=str(tmp_path / "custom.sqlite3"))
        assert str(base.DATABASES["default"]["NAME"]).endswith("custom.sqlite3")

    def test_mysql_full_mapping(self, monkeypatch, tmp_path):
        base = load_base(
            monkeypatch,
            tmp_path,
            DB_CONNECTION="mysql",
            DB_HOST="db.internal",
            DB_PORT="3307",
            DB_DATABASE="blessing",
            DB_USERNAME="bss",
            DB_PASSWORD="secret",
        )
        db = base.DATABASES["default"]
        assert db["ENGINE"] == "django.db.backends.mysql"
        assert db["HOST"] == "db.internal"
        assert db["PORT"] == "3307"
        assert db["NAME"] == "blessing"
        assert db["USER"] == "bss"
        assert db["PASSWORD"] == "secret"

    def test_pgsql_mapping_with_default_port(self, monkeypatch, tmp_path):
        base = load_base(monkeypatch, tmp_path, DB_CONNECTION="pgsql")
        db = base.DATABASES["default"]
        assert db["ENGINE"] == "django.db.backends.postgresql"
        assert db["HOST"] == "127.0.0.1"
        assert db["PORT"] == "5432"

    def test_mysql_default_host_port(self, monkeypatch, tmp_path):
        base = load_base(monkeypatch, tmp_path, DB_CONNECTION="mysql")
        db = base.DATABASES["default"]
        assert db["HOST"] == "127.0.0.1"
        assert db["PORT"] == "3306"

    def test_unknown_connection_rejected(self, monkeypatch, tmp_path):
        with pytest.raises(ImproperlyConfigured, match="DB_CONNECTION"):
            load_base(monkeypatch, tmp_path, DB_CONNECTION="oracle")


class TestSecretKey:
    def test_read_from_environment(self, monkeypatch, tmp_path):
        base = load_base(monkeypatch, tmp_path, SECRET_KEY="from-env")
        assert base.SECRET_KEY == "from-env"

    def test_ephemeral_key_in_debug_when_missing(self, monkeypatch, tmp_path):
        # reload 返回同一模块对象，必须在下次加载前捕获值
        first_key = load_base(monkeypatch, tmp_path).SECRET_KEY
        second_key = load_base(monkeypatch, tmp_path).SECRET_KEY
        # 开发模式生成进程内临时密钥，两次加载不同
        assert first_key
        assert second_key != first_key

    def test_missing_key_rejected_in_production(self, monkeypatch, tmp_path):
        with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
            load_base(monkeypatch, tmp_path, DEBUG="false")

    def test_production_key_from_env_accepted(self, monkeypatch, tmp_path):
        base = load_base(monkeypatch, tmp_path, DEBUG="false", SECRET_KEY="prod-key")
        assert base.SECRET_KEY == "prod-key"


class TestEnvFile:
    def test_env_file_loaded(self, monkeypatch, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET_KEY=from-file\nDB_CONNECTION=mysql\n", encoding="utf-8")
        base = load_base(monkeypatch, tmp_path, __env_file=str(env_file))
        assert base.SECRET_KEY == "from-file"
        assert base.DATABASES["default"]["ENGINE"] == "django.db.backends.mysql"

    def test_real_environment_takes_precedence_over_env_file(self, monkeypatch, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET_KEY=from-file\n", encoding="utf-8")
        base = load_base(monkeypatch, tmp_path, SECRET_KEY="from-env", __env_file=str(env_file))
        assert base.SECRET_KEY == "from-env"


class TestBooleanParsing:
    def test_missing_debug_defaults_true(self, monkeypatch, tmp_path):
        base = load_base(monkeypatch, tmp_path, SECRET_KEY="x")
        assert base.DEBUG is True

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values(self, monkeypatch, tmp_path, raw):
        base = load_base(monkeypatch, tmp_path, DEBUG=raw, SECRET_KEY="x")
        assert base.DEBUG is True

    # 空串语义为「未设置」，走默认值（见 test_missing_debug_defaults_true）
    @pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off"])
    def test_falsy_values(self, monkeypatch, tmp_path, raw):
        base = load_base(monkeypatch, tmp_path, DEBUG=raw, SECRET_KEY="x")
        assert base.DEBUG is False


class TestAllowedHosts:
    def test_unset_yields_empty_list(self, monkeypatch, tmp_path):
        base = load_base(monkeypatch, tmp_path)
        assert base.ALLOWED_HOSTS == []

    def test_comma_separated_parsing(self, monkeypatch, tmp_path):
        base = load_base(monkeypatch, tmp_path, ALLOWED_HOSTS="skin.example.com, cdn.example.com")
        assert base.ALLOWED_HOSTS == ["skin.example.com", "cdn.example.com"]


class TestModeModules:
    def test_dev_defaults_to_debug(self, monkeypatch, tmp_path):
        import config.settings.dev as dev

        module = importlib.reload(dev)
        assert module.DEBUG is True

    def test_prod_forces_debug_false(self, monkeypatch, tmp_path):
        import config.settings.prod as prod

        monkeypatch.setenv("SECRET_KEY", "prod-test")
        monkeypatch.setenv("DEBUG", "true")
        module = importlib.reload(prod)
        assert module.DEBUG is False

    def test_test_module_uses_in_memory_sqlite_and_fixed_key(self):
        import config.settings.test as test_mod

        module = importlib.reload(test_mod)
        assert module.DATABASES["default"]["NAME"] == ":memory:"
        assert module.SECRET_KEY.startswith("test-only-fixed-secret-key")
        assert len(module.SECRET_KEY) >= 32  # PyJWT HMAC 推荐最小长度
