"""客户端 IP 提取策略测试（config.clientip）。

安全锚点：XFF 首跳可被客户端伪造，必须取末跳（自家反代追加段）。
"""

from django.test import RequestFactory

from config.clientip import get_client_ip


def _rf(**meta):
    return RequestFactory().get("/", **meta)


class TestGetClientIp:
    def test_no_forwarded_falls_back_to_remote_addr(self):
        assert get_client_ip(_rf(REMOTE_ADDR="1.2.3.4")) == "1.2.3.4"

    def test_single_hop_xff(self):
        req = _rf(REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="9.9.9.9")
        assert get_client_ip(req) == "9.9.9.9"

    def test_multi_hop_takes_last(self):
        """多跳 XFF 取末跳：首段为客户端可伪造值，不可采信。"""
        req = _rf(
            REMOTE_ADDR="10.0.0.1",
            HTTP_X_FORWARDED_FOR="9.9.9.9, 8.8.8.8, 10.0.0.1",
        )
        assert get_client_ip(req) == "10.0.0.1"

    def test_first_hop_not_trusted(self):
        """伪造首段（攻击者自带 XFF）不得成为取值结果。"""
        req = _rf(REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="1.1.1.1, 2.2.2.2")
        assert get_client_ip(req) != "1.1.1.1"
        assert get_client_ip(req) == "2.2.2.2"

    def test_empty_forwarded_falls_back(self):
        req = _rf(REMOTE_ADDR="3.3.3.3", HTTP_X_FORWARDED_FOR="")
        assert get_client_ip(req) == "3.3.3.3"

    def test_blank_entries_skipped(self):
        req = _rf(REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR=" , 5.5.5.5 ,")
        assert get_client_ip(req) == "5.5.5.5"
