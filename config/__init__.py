"""config 包初始化：在任何 settings 模块加载前完成全局准备。

MySQL 驱动 shim：Django 的 mysql 后端硬性 import MySQLdb（见
django/db/backends/mysql/base.py，5.2 不支持 connector 回落）。
PyMySQL 为纯 Python 实现（无系统依赖，镜像保持 slim），经
install_as_MySQLdb() 满足该接口 —— Django 官方文档认可的替代方案。

未安装 PyMySQL 的环境（纯 SQLite 直部署）静默跳过，不受影响。
"""

try:
    import pymysql

    pymysql.install_as_MySQLdb()
except ImportError:  # pragma: no cover - 仅 SQLite 部署环境走到
    pass
