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


class TestProxyDepth:
    """前置 CDN 等多层可信代理：取 XFF 右起第 CLIENT_IP_PROXY_DEPTH 跳。"""

    def test_cdn_depth_two_takes_second_from_right(self, settings):
        """CDN+反代：右起第 2 跳为真实客户端，末跳是 CDN 节点 IP。"""
        settings.CLIENT_IP_PROXY_DEPTH = 2
        req = _rf(
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_FORWARDED_FOR="9.9.9.9, 203.0.113.7, 198.51.100.9",
        )
        assert get_client_ip(req) == "203.0.113.7"

    def test_cdn_depth_two_strips_client_forgery(self, settings):
        """直连 CDN 的伪造首段照常剥离（伪造段永远在 XFF 左侧）。"""
        settings.CLIENT_IP_PROXY_DEPTH = 2
        req = _rf(HTTP_X_FORWARDED_FOR="1.1.1.1, 203.0.113.7, 198.51.100.9")
        assert get_client_ip(req) == "203.0.113.7"

    def test_cdn_depth_two_short_xff_falls_back_to_last(self, settings):
        """CDN 未按预期追加（仅 1 跳）→ 回落末跳而非报错/取左段。"""
        settings.CLIENT_IP_PROXY_DEPTH = 2
        req = _rf(HTTP_X_FORWARDED_FOR="203.0.113.7")
        assert get_client_ip(req) == "203.0.113.7"

    def test_depth_one_is_default_behavior(self, settings):
        """默认/显式 1 保持单层反代语义：末跳。"""
        settings.CLIENT_IP_PROXY_DEPTH = 1
        req = _rf(HTTP_X_FORWARDED_FOR="9.9.9.9, 10.0.0.1")
        assert get_client_ip(req) == "10.0.0.1"

    def test_oversized_depth_never_reaches_client_controlled_left(self, settings):
        """层多配的警示基线：客户端自带伪造段凑够跳数时会取到伪造段——
        故层数宁少勿多；跳数不足时则安全回落末跳（见 short_xff 用例）。"""
        settings.CLIENT_IP_PROXY_DEPTH = 3
        req = _rf(HTTP_X_FORWARDED_FOR="1.1.1.1, 203.0.113.7, 198.51.100.9")
        assert get_client_ip(req) == "1.1.1.1"
