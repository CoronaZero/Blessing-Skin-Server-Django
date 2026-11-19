"""子进程脚本：DatabaseCache 跨句柄共享语义验证。

由 test_cache_backend.py 以独立进程驱动——绕开 pytest-django 对
default 连接的事务包裹与 :memory: 限制，用真实文件库还原生产形态。
进程输出 OK / FAIL:<原因> 供测试断言。
"""

import os
import sqlite3
import sys
from pathlib import Path

# 以脚本路径直接运行时把项目根插入 sys.path（venv python 解析 config）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
django.setup()

# 1. 文件库 + django_cache_table（等价生产 createcachetable 之后）
db_file = sys.argv[1]
conn = sqlite3.connect(db_file)
conn.execute(
    "CREATE TABLE IF NOT EXISTS django_cache_table ("
    "cache_key VARCHAR(255) PRIMARY KEY, value BLOB, expires DATETIME)"
)
conn.commit()
conn.close()

from django.conf import settings  # noqa: E402
from django.db import connections  # noqa: E402

# ConnectionHandler 在 django.setup() 时快照过 DATABASES：改 settings
# 不够，须同步 connections.settings 并关闭已建的连接（热切文件库）
connections["default"].close()
settings.DATABASES["default"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": db_file,
}
connections.settings["default"] = dict(settings.DATABASES["default"])
connections["default"].settings_dict.update(settings.DATABASES["default"])

# 2. 两个独立 DatabaseCache 句柄（= 两个 worker 的 cache 对象）
from django.core.cache.backends.db import DatabaseCache  # noqa: E402

worker_a = DatabaseCache("django_cache_table", {})
worker_b = DatabaseCache("django_cache_table", {})

worker_a.set("msa_oauth_state:token-x", {"intent": "login"}, 600)
if worker_b.get("msa_oauth_state:token-x") != {"intent": "login"}:
    print("FAIL:worker_b 不可见 worker_a 写入的 state")
    sys.exit(1)

# 3. oauth_bridge.state 工作流跨句柄闭环（发起句柄 A、回调句柄 B）
import oauth_bridge.state as state_mod  # noqa: E402


class _Session(dict):
    pass


class _Request:
    session = _Session()


state_mod.cache = worker_a
req = _Request()
token = state_mod.put_state({"intent": "genuine"}, req)
if req.session.get(state_mod.SESSION_KEY) != token:
    print("FAIL:put_state 未记录会话")
    sys.exit(1)

state_mod.cache = worker_b  # 回调落到另一 worker
payload = state_mod.pop_state(token, req)
if payload != {"intent": "genuine"}:
    print(f"FAIL:pop_state 跨句柄失败：{payload!r}")
    sys.exit(1)
if worker_a.get("msa_oauth_state:" + token) is not None:
    print("FAIL:state 未一次性作废")
    sys.exit(1)

print("OK")
