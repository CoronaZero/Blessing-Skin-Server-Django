"""Phase 0 风险项验证脚本：DOT password grant 可行性（运行时探测）。

背景：PHP 原版用 Laravel Passport，其 /oauth/token 的 password grant
被皮肤站客户端广泛使用（guzzle 拿 token 调 API）。
重写版用 django-oauth-toolkit，必须确认等价能力存在。

结论依据：
1. oauthlib 4.0.1 保留 ResourceOwnerPasswordCredentialsGrant（RFC 6749 §4.3）
2. DOT 3.4.1 默认 OAUTH2_GRANT_TYPES_SUPPORTED 含 "password"
3. DOT validate_user 委托 django.contrib.auth.authenticate()
   → 我们的自定义 hasher / 认证后端可透明接入
"""

import inspect

from oauth2_provider import oauth2_validators
from oauth2_provider.views import TokenView
from oauthlib.oauth2 import Server


def test_oauthlib_server_registers_password_grant_by_default():
    """oauthlib.Server 构造函数默认注册 password grant。"""
    src = inspect.getsource(Server.__init__)
    assert "ResourceOwnerPasswordCredentialsGrant" in src


def test_dot_default_grants_include_password():
    """DOT 默认配置的 grant 列表含 password（项目未覆盖 OAUTH2_PROVIDER 时）。"""
    from oauth2_provider.settings import oauth2_settings

    assert "password" in oauth2_settings.OAUTH2_GRANT_TYPES_SUPPORTED


def test_dot_validate_user_delegates_to_django_authenticate():
    """validate_user 内部调用 django.contrib.auth.authenticate。"""
    src = inspect.getsource(oauth2_validators.OAuth2Validator.validate_user)
    assert "authenticate(" in src


def test_token_view_importable():
    """DOT TokenView（/oauth/token 视图）可导入。"""
    assert TokenView is not None
