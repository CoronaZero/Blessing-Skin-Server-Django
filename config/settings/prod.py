"""生产配置：强制 DEBUG=False。

在导入 base 前锁定环境开关，确保 base 中的 SECRET_KEY 缺失
防护在生产入口（wsgi/asgi）正确触发：gunicorn 直接以本模块启动时，
若未配置 SECRET_KEY 将拒绝启动。
"""

import os

os.environ["DEBUG"] = "false"

from .base import *  # noqa: F401,F403,E402

DEBUG = False
