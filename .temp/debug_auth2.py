import json
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

django.setup()

from django.test import Client  # noqa: E402
from django.test.runner import DiscoverRunner  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
runner = DiscoverRunner(verbosity=0)
old_config = runner.setup_databases()

from accounts.models import User  # noqa: E402
from players.models import Player  # noqa: E402

u = User.objects.create_user(
    email="y@x.com", nickname="y", password="password8", verified=True
)
Player.objects.create(uid=u, name="Steve")

c = Client()
resp = c.post(
    "/api/yggdrasil/authserver/authenticate",
    data=json.dumps({"username": "y@x.com", "password": "password8", "clientToken": "fixed-client"}),
    content_type="application/json",
)
print("status:", resp.status_code)
print("body:", resp.content.decode()[:400])

runner.teardown_databases(old_config)
