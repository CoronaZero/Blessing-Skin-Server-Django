import json
import os

import django

os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.test"
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
    data=json.dumps({"username": "y@x.com", "password": "password8"}),
    content_type="application/json",
)
print("auth:", resp.status_code, resp.content.decode()[:300])

hello = c.get("/api/yggdrasil/")
print("hello:", hello.status_code, hello.content.decode()[:200])

runner.teardown_databases(old_config)
