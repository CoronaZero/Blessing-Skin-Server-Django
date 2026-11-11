"""pytest 全局 fixtures。"""

import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    """每用例清空 Django cache（登录失败计数、邮件限速键等跨用例隔离）。"""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()
