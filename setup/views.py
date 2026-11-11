"""setup 应用：首次部署安装向导。

对照原版 SetupController / install.lock：
- 向导仅在未安装（无锁文件）时可用，装完落锁
- 收集站点名与管理员 → 初始化 options 全量默认值 →
  生成 Yggdrasil 材质签名 RSA 密钥对 → 建超管 → 自动登录
"""

import json
from pathlib import Path

from django.contrib.auth import login as auth_login
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from config.jsonapi import SUCCESS, json_response
from options.models import Option

# 锁文件位置：项目根 <BASE_DIR>/install.lock（对照原版 storage/install.lock）
LOCK_FILE = Path(__file__).resolve().parent.parent / "install.lock"

# 安装时写入的全量 option 默认值（对照原版 import_options 迁移）
DEFAULT_OPTIONS = {
    "site_name": "",
    "site_description": "",
    "player_name_rule": "official",
    "custom_player_name_regexp": "",
    "player_name_length_min": "4",
    "player_name_length_max": "16",
    "regs_per_ip": "3",
    "register_with_player_name": "0",
    "score_per_storage": "1",
    "private_score_per_storage": "1",
    "score_per_closet_item": "1",
    "score_per_sign": "5",
    "score_award_per_texture": "0",
    "user_initial_score": "0",
    "max_upload_file_size": "2048",
    "max_texture_width": "8192",
    "require_verification": "0",
    "invitation_required": "0",
    "disable_registration": "0",
    "captcha_provider": "",
    "turnstile_sitekey": "",
    "turnstile_secret": "",
    "login_enumeration_protection": "1",
    "player_limit_per_user": "0",
    # Yggdrasil（对照原版 yggdrasil-api 启用回调）
    "ygg_uuid_algorithm": "v4",
    "ygg_token_expire_1": "259200",
    "ygg_token_expire_2": "604800",
    "ygg_tokens_limit": "10",
    "ygg_rate_limit": "1000",
    "ygg_skin_domain": "",
    "ygg_search_profile_max": "5",
}


def installation_locked() -> bool:
    return LOCK_FILE.exists()


@require_GET
def wizard(request):
    if installation_locked():
        return redirect("/")
    return render(request, "setup.html")


@require_POST
def submit(request):
    if installation_locked():
        return json_response("站点已完成安装，如需重新安装请先删除 install.lock")

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return json_response("请求数据格式错误")

    site_name = (payload.get("site_name") or "").strip()
    admin_email = (payload.get("admin_email") or "").strip()
    admin_password = payload.get("admin_password") or ""
    admin_nickname = (payload.get("admin_nickname") or "Admin").strip()

    if not site_name:
        return json_response("请填写站点名称")
    if "@" not in admin_email:
        return json_response("请填写管理员邮箱")
    if len(admin_password) < 8:
        return json_response("管理员密码至少 8 位")

    from accounts.models import User

    if User.objects.exists():
        return json_response("数据库已有用户，疑似非全新安装，已中止")

    admin = User.objects.create_superuser(
        email=admin_email, nickname=admin_nickname, password=admin_password
    )

    options = dict(DEFAULT_OPTIONS)
    options["site_name"] = site_name
    Option.objects.bulk_set(options)
    _generate_ygg_keypair()

    LOCK_FILE.write_text("installed\n", encoding="utf-8")
    auth_login(request, admin)
    return JsonResponse({
        "code": SUCCESS,
        "message": "安装完成",
        "data": {"redirectTo": "/admin/"},
    })


def _generate_ygg_keypair():
    """生成 2048 位 RSA 密钥对：私钥签材质，公钥入发现端点（对照原版）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    if Option.objects.get_value("ygg_private_key"):
        return

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    Option.objects.set("ygg_private_key", private_pem)
    Option.objects.set("ygg_public_key", public_pem)
