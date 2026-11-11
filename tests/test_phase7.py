"""Phase 7 集成测试：安装向导 + 批量导入命令。"""

import io
import json

import pytest
from PIL import Image

from accounts.models import User
from options.models import Option

pytestmark = pytest.mark.django_db


def png_bytes(size, color=(255, 0, 0, 255)):
    img = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---- 安装向导 ----


class TestSetupWizard:
    def test_wizard_available_when_unlocked(self, client, tmp_path, monkeypatch):
        from setup import views

        monkeypatch.setattr(views, "LOCK_FILE", tmp_path / "no.lock")
        resp = client.get("/setup/")
        assert resp.status_code == 200

    def test_wizard_redirects_when_locked(self, client, tmp_path, monkeypatch):
        from setup import views

        lock = tmp_path / "install.lock"
        lock.write_text("installed")
        monkeypatch.setattr(views, "LOCK_FILE", lock)
        resp = client.get("/setup/")
        assert resp.status_code == 302

    def test_submit_creates_admin_and_options(self, client, tmp_path, monkeypatch):
        from setup import views

        monkeypatch.setattr(views, "LOCK_FILE", tmp_path / "install.lock")

        resp = client.post(
            "/setup/",
            data=json.dumps({
                "site_name": "测试站",
                "admin_email": "admin@x.com",
                "admin_password": "password8",
                "admin_nickname": "站长",
            }),
            content_type="application/json",
        )
        assert resp.json()["code"] == 0

        admin = User.objects.get(email="admin@x.com")
        assert admin.permission == User.Permission.SUPER_ADMIN
        assert Option.objects.get_value("site_name") == "测试站"
        # 全量默认值
        assert Option.objects.get_value("regs_per_ip") == "3"
        assert Option.objects.get_value("ygg_token_expire_1") == "259200"
        # RSA 密钥对已生成
        assert Option.objects.get_value("ygg_private_key", "").startswith("-----BEGIN")
        assert Option.objects.get_value("ygg_public_key", "").startswith("-----BEGIN")
        # 锁文件落盘
        assert (tmp_path / "install.lock").exists()

    def test_second_submit_rejected(self, client, tmp_path, monkeypatch):
        from setup import views

        lock = tmp_path / "install.lock"
        lock.write_text("installed")
        monkeypatch.setattr(views, "LOCK_FILE", lock)
        resp = client.post("/setup/", data="{}", content_type="application/json")
        assert resp.json()["code"] == 1

    def test_submit_with_existing_users_aborts(self, client, tmp_path, monkeypatch):
        from setup import views

        monkeypatch.setattr(views, "LOCK_FILE", tmp_path / "install.lock")
        User.objects.create_user(email="old@x.com", nickname="old", password="password8")
        resp = client.post(
            "/setup/",
            data=json.dumps({
                "site_name": "S", "admin_email": "a@x.com",
                "admin_password": "password8",
            }),
            content_type="application/json",
        )
        assert resp.json()["code"] == 1
        assert "非全新安装" in resp.json()["message"]

    def test_weak_admin_password_rejected(self, client, tmp_path, monkeypatch):
        from setup import views

        monkeypatch.setattr(views, "LOCK_FILE", tmp_path / "install.lock")
        resp = client.post(
            "/setup/",
            data=json.dumps({
                "site_name": "S", "admin_email": "a@x.com", "admin_password": "123",
            }),
            content_type="application/json",
        )
        assert resp.json()["code"] == 1


# ---- 批量导入 ----


class TestImportTextures:
    def _run(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("import_textures", *args, stdout=out)
        return out.getvalue()

    def _dir(self, tmp_path, files: dict):
        target = tmp_path if files else tmp_path
        target.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            target.joinpath(name).write_bytes(data)
        return str(target)

    def test_import_naming_conventions(self, tmp_path, settings):
        from django.core.files.storage import FileSystemStorage

        storage_root = tmp_path / "storage"
        settings.TEXTURE_STORAGE_ROOT = str(storage_root)
        from textures import storage as storage_mod

        storage_mod.texture_storage._storage = FileSystemStorage(location=str(storage_root))

        User.objects.create_user(
            email="op@x.com", nickname="op", password="password8",
            permission=User.Permission.ADMIN,
        )
        directory = self._dir(tmp_path / "pngs", {
            "史蒂夫.png": png_bytes((64, 32)),                    # steve
            "爱莉克丝.alex.png": png_bytes((64, 64)),             # alex（1:1）
            "红色披风.cape.png": png_bytes((64, 32), (0, 255, 0)),  # cape（2:1，异色防 hash 撞
            "坏图.png": b"not a png",                              # 无效
        })
        output = self._run(directory, "--public")

        assert "导入 3" in output
        assert "无效 1" in output
        from textures.models import Texture

        assert Texture.objects.get(name="史蒂夫").type == "steve"
        assert Texture.objects.get(name="爱莉克丝").type == "alex"
        assert Texture.objects.get(name="红色披风").type == "cape"
        for t in Texture.objects.all():
            assert t.public is True
            assert storage_mod.texture_storage.exists(t.hash)

    def test_import_dry_run(self, tmp_path):
        User.objects.create_user(
            email="op2@x.com", nickname="op2", password="password8",
            permission=User.Permission.ADMIN,
        )
        directory = self._dir(tmp_path / "pngs2", {"a.png": png_bytes((64, 32))})
        output = self._run(directory, "--dry-run")
        assert "[dry-run]" in output
        from textures.models import Texture

        assert Texture.objects.count() == 0
        _ = output

    def test_import_requires_admin(self, tmp_path):
        directory = self._dir(tmp_path / "empty", {})
        output = self._run(directory)
        assert "没有 PNG" in output
