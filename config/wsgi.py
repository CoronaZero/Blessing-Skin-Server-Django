"""WSGI 入口（gunicorn 默认加载）。生产默认使用 prod 配置。"""

import os

from django.core.wsgi import get_wsgi_application

default_settings = "config.settings.prod"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", default_settings)

application = get_wsgi_application()
