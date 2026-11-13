"""Yggdrasil JWT 工具测试（tokens.py）。

密钥隔离锚点：accessToken 签名密钥与 Django session/CSRF 使用的
SECRET_KEY 通过 HMAC 固定盐派生相互隔离。
"""

import jwt
import pytest
from django.conf import settings

from yggdrasil import tokens

pytestmark = pytest.mark.django_db


class TestJwtSecretIsolation:
    def test_token_roundtrip(self):
        token = tokens.issue_access_token("a" * 32, "ygg-t", 60, spr="b" * 32)
        payload = tokens.decode_access_token(token)
        assert payload["sub"] == "a" * 32
        assert payload["yggt"] == "ygg-t"
        assert payload["spr"] == "b" * 32

    def test_token_rejects_raw_secret_key(self):
        """JWT 不得能用裸 SECRET_KEY 校验通过（用途隔离可验证）。"""
        token = tokens.issue_access_token("a" * 32, "t", 60)
        with pytest.raises(jwt.PyJWTError):
            jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])

    def test_expired_token_rejected(self):
        token = tokens.issue_access_token("a" * 32, "t", -1)
        assert tokens.decode_access_token(token) is None

    def test_garbage_token_rejected(self):
        assert tokens.decode_access_token("not-a-jwt") is None
