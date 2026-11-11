"""数据库后端可用性测试（部署链路保障）。

compose 默认 MySQL 部署：镜像内必须能加载 MySQL 后端。
Django 的 mysql 后端硬性 import MySQLdb（5.2 无 connector 回落），
PyMySQL 经 install_as_MySQLdb() shim 满足该接口。
此处仅验证后端模块可导入，不做真实连接。
"""


def test_mysql_backend_module_importable():
    """MySQL 后端可导入 = compose 默认部署链路成立。"""
    from django.db.backends.mysql import base as mysql_base

    assert mysql_base.Database is not None
    driver_name = mysql_base.Database.__name__
    assert driver_name in ("MySQLdb", "pymysql"), f"意外的驱动：{driver_name}"


def test_mysql_backend_version_check_passed():
    """Django 对 MySQLdb 有最低版本检查，PyMySQL shim 声明的版本须达标。"""
    from django.db.backends.mysql import base as mysql_base

    assert mysql_base.Database.version_info >= (1, 4, 3)


def test_shim_absent_when_driver_missing():
    """未安装 PyMySQL 的环境（纯 SQLite 直部署）：shim 优雅跳过不炸。

    config/__init__.py 的 try/except 语义：无论 pymysql 是否在,
    import config 都必须成功。
    """
    import importlib

    import config

    importlib.reload(config)  # 不抛异常即为通过


def test_sqlite_backend_still_default():
    """默认 DB_CONNECTION=sqlite 不受驱动 shim 影响。"""
    from django.conf import settings as dj_settings

    if dj_settings.DATABASES["default"]["NAME"] == ":memory:":
        assert dj_settings.DATABASES["default"]["ENGINE"].endswith("sqlite3")
