"""Microsoft OAuth 链路单元测试：授权 URL / 换 token / id_token 解析 /
Xbox 五步链路 / XErr 错误映射 / state 一次性 / 出站代理配置。"""

import pytest

from oauth_bridge import httpclient, msauth, options, state

pytestmark = pytest.mark.django_db


# ---- 可用性开关组合 ----


def set_option(name, value):
    from options.models import Option

    Option.objects.set(name, value)


class TestOptions:
    def test_defaults_off(self):
        assert not options.msa_configured()
        assert not options.msa_login_ready()
        assert not options.msa_register_ready()
        assert not options.msa_bind_ready()
        assert not options.passthrough_ready()
        assert options.unbind_cooldown_days() == 0
        assert httpclient._proxies() is None

    def test_configured_requires_both_credentials(self):
        set_option("microsoft_client_id", "cid")
        assert not options.msa_configured()
        set_option("microsoft_client_secret", "secret")
        assert options.msa_configured()

    def test_flags_matrix(self):
        set_option("microsoft_client_id", "cid")
        set_option("microsoft_client_secret", "secret")
        assert not options.msa_login_ready()
        set_option("microsoft_oauth_enabled", "1")
        set_option("microsoft_login_enabled", "true")
        assert options.msa_login_ready()
        assert not options.msa_register_ready()
        set_option("microsoft_register_enabled", "on")
        assert options.msa_register_ready()
        assert not options.msa_bind_ready()
        assert not options.passthrough_ready()
        set_option("microsoft_bind_enabled", "1")
        set_option("microsoft_passthrough_enabled", "1")
        assert options.msa_bind_ready()
        assert options.passthrough_ready()

    def test_unbind_cooldown_days(self):
        set_option("microsoft_unbind_cooldown_days", "7")
        assert options.unbind_cooldown_days() == 7
        set_option("microsoft_unbind_cooldown_days", "abc")
        assert options.unbind_cooldown_days() == 0


class TestProxyConfig:
    def test_proxy_plain_address_gets_http_scheme(self):
        set_option("microsoft_outbound_proxy", "127.0.0.1:7890")
        assert httpclient._proxies() == {
            "http": "http://127.0.0.1:7890",
            "https": "http://127.0.0.1:7890",
        }

    def test_proxy_with_scheme_kept(self):
        set_option("microsoft_outbound_proxy", "https://p.example.com:3128")
        assert httpclient._proxies() == {
            "http": "https://p.example.com:3128",
            "https": "https://p.example.com:3128",
        }


# ---- 授权 URL / state ----


class TestAuthorizeUrl:
    def test_login_url_shape(self):
        url = msauth.authorize_url(
            client_id="cid", redirect_uri="https://s.test/oauth/microsoft/callback",
            scope=msauth.SCOPE_LOGIN, state="st-123",
        )
        assert url.startswith("https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?")
        assert "client_id=cid" in url
        assert "response_type=code" in url
        assert "state=st-123" in url
        assert "scope=openid+email+profile" in url

    def test_xbox_scope_url(self):
        url = msauth.authorize_url(
            client_id="cid", redirect_uri="https://s.test/cb",
            scope=msauth.SCOPE_XBOX, state="s",
        )
        assert "scope=XboxLive.signin" in url


class TestState:
    def _request(self):
        from django.test import RequestFactory

        req = RequestFactory().get("/")
        req.session = {}
        return req

    def test_roundtrip_single_use(self):
        req = self._request()
        token = state.put_state({"intent": "login", "next": "/user"}, req)
        assert token
        payload = state.pop_state(token, req)
        assert payload == {"intent": "login", "next": "/user"}
        # 一次性：再取为 None
        assert state.pop_state(token, req) is None

    def test_unknown_state(self):
        req = self._request()
        assert state.pop_state("nonexistent", req) is None

    def test_state_must_match_initiating_session(self):
        """授权码注入防护：state 换会话即失效。"""
        attacker_req = self._request()
        token = state.put_state({"intent": "login"}, attacker_req)
        # 受害者会话未发起过该流程 → 拒绝
        victim_req = self._request()
        assert state.pop_state(token, victim_req) is None
        # 攻击者自己的会话（未过期窗口内）仍可用一次
        payload = state.pop_state(token, attacker_req)
        assert payload == {"intent": "login"}

    def test_oversized_state_rejected(self):
        req = self._request()
        assert state.pop_state("x" * 129, req) is None


# ---- 登录身份（id_token 解析）----


class TestLoginIdentity:
    def _token_response(self, monkeypatch, id_token=None, status=200, error=None):
        calls = {}

        class FakeResp:
            def __init__(self, payload, status):
                self._payload = payload
                self.status_code = status

            def json(self):
                return self._payload

        def fake_post(url, *, json=None, data=None, timeout=None, headers=None):
            calls["url"] = url
            calls["data"] = data
            if error:
                raise error
            return FakeResp({"access_token": "at", "id_token": id_token}, status)

        monkeypatch.setattr(httpclient, "outbound_post", fake_post)
        return calls

    def test_success(self, monkeypatch):
        import base64
        import json

        def b64(obj):
            return base64.urlsafe_b64encode(
                json.dumps(obj).encode()
            ).decode().rstrip("=")

        claims = {"sub": "sub-1", "email": "a@live.com", "name": "A"}
        id_token = f"{b64({'alg': 'none'})}.{b64(claims)}.sig"
        calls = self._token_response(monkeypatch, id_token=id_token)
        identity = msauth.fetch_login_identity(
            client_id="cid", client_secret="sec", code="the-code",
            redirect_uri="https://s.test/oauth/microsoft/callback",
        )
        assert identity == {"sub": "sub-1", "email": "a@live.com", "name": "A"}
        assert calls["url"].endswith("/oauth2/v2.0/token")
        assert calls["data"]["grant_type"] == "authorization_code"
        assert calls["data"]["code"] == "the-code"

    def test_missing_id_token(self, monkeypatch):
        self._token_response(monkeypatch, id_token=None)
        with pytest.raises(msauth.MicrosoftAuthError):
            msauth.fetch_login_identity("cid", "sec", "code", "https://s.test/cb")

    def test_missing_sub(self, monkeypatch):
        import base64
        import json

        def b64(obj):
            return base64.urlsafe_b64encode(
                json.dumps(obj).encode()
            ).decode().rstrip("=")

        id_token = f"{b64({})}.{b64({'email': 'a@live.com'})}.sig"
        self._token_response(monkeypatch, id_token=id_token)
        with pytest.raises(msauth.MicrosoftAuthError):
            msauth.fetch_login_identity("cid", "sec", "code", "https://s.test/cb")

    def test_token_endpoint_http_error(self, monkeypatch):
        import requests

        self._token_response(monkeypatch, error=requests.RequestException("boom"))
        with pytest.raises(msauth.MicrosoftAuthError):
            msauth.fetch_login_identity("cid", "sec", "code", "https://s.test/cb")


# ---- Xbox 五步链路 ----


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def b64_raw(obj):
    import base64
    import json

    return base64.b64encode(json.dumps(obj).encode()).decode()


def mojang_profile_payload():
    return {
        "id": "abc123def456",
        "name": "Notch",
        "properties": [
            {
                "name": "textures",
                "value": b64_raw({
                    "timestamp": 1,
                    "profileId": "abc123def456",
                    "profileName": "Notch",
                    "textures": {
                        "SKIN": {"url": "http://textures.minecraft.net/texture/skinhash"},
                    },
                }),
            }
        ],
    }


class TestXboxChain:
    def _patch_chain(self, monkeypatch, *, xsts_payload=None, mc_login_payload=None,
                     mc_login_status=401, profile_payload=None,
                     profile_status=200, xsts_status=200):
        posts = []
        gets = []

        def fake_post(url, *, json=None, data=None, timeout=None, headers=None):
            posts.append((url, json))
            if url == msauth.XBL_AUTH_URL:
                return FakeResp({"Token": "xbl-tok",
                                 "DisplayClaims": {"xui": [{"uhs": "uhs-1"}]}})
            if url == msauth.XSTS_AUTH_URL:
                # 真实 XSTS 在 XErr 时回 401；Token 成功时回 200
                payload = xsts_payload if xsts_payload is not None \
                    else {"Token": "xsts-tok"}
                status = xsts_status if xsts_payload is not None else 200
                return FakeResp(payload, status)
            if url == msauth.MC_LOGIN_URL:
                if mc_login_payload is None:
                    return FakeResp({"access_token": "mc-at"})
                return FakeResp(mc_login_payload, status=mc_login_status)
            raise AssertionError(f"unexpected POST {url}")

        def fake_get(url, *, params=None, timeout=None, headers=None):
            gets.append((url, headers))
            return FakeResp(profile_payload if profile_payload is not None
                            else mojang_profile_payload(), profile_status)

        monkeypatch.setattr(httpclient, "outbound_post", fake_post)
        monkeypatch.setattr(httpclient, "outbound_get", fake_get)
        return posts, gets

    def test_success_chain(self, monkeypatch):
        posts, gets = self._patch_chain(monkeypatch)
        profile, mc_token = msauth.xbox_authenticate("msa-at")
        assert profile["id"] == "abc123def456"
        assert profile["name"] == "Notch"
        assert mc_token == "mc-at"
        # 请求体契约断言
        assert posts[0][1]["Properties"]["RpsTicket"] == "d=msa-at"
        assert posts[1][1]["Properties"]["UserTokens"] == ["xbl-tok"]
        assert posts[1][1]["RelyingParty"] == "rp://api.minecraftservices.com/"
        assert posts[2][1] == {"identityToken": "XBL3.0 x=uhs-1;xsts-tok"}
        assert gets[0][0] == msauth.MC_PROFILE_URL
        assert gets[0][1]["Authorization"] == "Bearer mc-at"

    @pytest.mark.parametrize("xerr,message,status", [
        (2148916233, "Xbox 档案", 401),
        (2148916238, "家庭组", 401),
        (2148916227, "封禁", 403),
    ])
    def test_xsts_errors(self, monkeypatch, xerr, message, status):
        self._patch_chain(
            monkeypatch,
            xsts_payload={"XErr": xerr}, xsts_status=status,
        )
        with pytest.raises(msauth.MicrosoftAuthError) as exc_info:
            msauth.xbox_authenticate("msa-at")
        assert message in exc_info.value.message

    def test_xsts_200_with_xerr_is_error(self, monkeypatch):
        """PHP/HMCL 同语义：XErr 在响应体即失败，不看 HTTP status。"""
        self._patch_chain(
            monkeypatch, xsts_payload={"XErr": 2148916238}, xsts_status=200,
        )
        with pytest.raises(msauth.MicrosoftAuthError) as exc_info:
            msauth.xbox_authenticate("msa-at")
        assert "家庭组" in exc_info.value.message

    def test_mc_login_rejected(self, monkeypatch):
        self._patch_chain(monkeypatch, mc_login_payload={"error": "UNAUTHORIZED"})
        with pytest.raises(msauth.MicrosoftAuthError) as exc_info:
            msauth.xbox_authenticate("msa-at")
        # 上游状态码透出，不再冒用“未购买”文案
        assert "HTTP 401" in exc_info.value.message
        assert "未购买" not in exc_info.value.message

    def test_mc_login_app_registration_rejected(self, monkeypatch):
        """Mojang 审批政策：新 Azure 应用未过审 → 403 Invalid app registration。"""
        self._patch_chain(
            monkeypatch,
            mc_login_payload={
                "error": "Forbidden",
                "errorMessage": (
                    "Invalid app registration, see https://aka.ms/AppRegInfo "
                    "for more information"
                ),
            },
            mc_login_status=403,
        )
        with pytest.raises(msauth.MicrosoftAuthError) as exc_info:
            msauth.xbox_authenticate("msa-at")
        assert "审批" in exc_info.value.message
        assert "mce-reviewappid" in exc_info.value.message

    def test_profile_not_purchased(self, monkeypatch):
        self._patch_chain(
            monkeypatch,
            profile_payload={"path": "/minecraft/profile", "error": "NOT_FOUND"},
            profile_status=404,
        )
        with pytest.raises(msauth.MicrosoftAuthError) as exc_info:
            msauth.xbox_authenticate("msa-at")
        assert "未购买" in exc_info.value.message

    def test_profile_constraint_violation(self, monkeypatch):
        self._patch_chain(
            monkeypatch,
            profile_payload={"path": "/minecraft/profile",
                             "error": "CONSTRAINT_VIOLATION"},
        )
        with pytest.raises(msauth.MicrosoftAuthError) as exc_info:
            msauth.xbox_authenticate("msa-at")
        assert "地区" in exc_info.value.message


class TestOutboundHeaders:
    """出站请求头与重试：自定义 UA + Accept（对照 minecraft-launcher-lib）。"""

    def test_default_headers_shape(self):
        assert "python-requests" not in httpclient.USER_AGENT.lower()
        assert httpclient.JSON_HEADERS["Accept"] == "application/json"
        assert httpclient.USER_AGENT

    def test_merged_headers_override(self):
        merged = httpclient._merged_headers({"Authorization": "Bearer x"})
        assert merged["User-Agent"] == httpclient.USER_AGENT
        assert merged["Accept"] == "application/json"
        assert merged["Authorization"] == "Bearer x"

    def test_retry_on_network_error_then_success(self, monkeypatch):
        """网络异常重试：首次抛 RequestException，第二次成功。"""
        import requests

        calls = []

        class FakeResp:
            status_code = 200

            def json(self):
                return {"ok": True}

        def fake_get(url, *, params=None, headers=None, timeout=None,
                     proxies=None):
            calls.append(1)
            if len(calls) == 1:
                raise requests.RequestException("timeout")
            return FakeResp()

        monkeypatch.setattr(httpclient._session, "get", fake_get)
        resp = httpclient.outbound_get("https://x.test", retries=2)
        assert resp.json() == {"ok": True}
        assert len(calls) == 2

    def test_retry_exhausted_raises(self, monkeypatch):
        import requests

        def fake_get(url, **kwargs):
            raise requests.RequestException("down")

        monkeypatch.setattr(httpclient._session, "get", fake_get)
        with pytest.raises(requests.RequestException):
            httpclient.outbound_get("https://x.test", retries=3)
