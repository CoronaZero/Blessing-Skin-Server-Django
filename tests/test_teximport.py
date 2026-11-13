"""teximport 全链路测试：URL 净化/解析、SSRF 防护、入队、审核、拉取、计分"""

import io
import ipaddress

import pytest
from django.core.files.storage import FileSystemStorage
from PIL import Image

from accounts.models import User
from notifications.models import Notification
from options.models import Option
from teximport import fetcher
from teximport.models import ImportRequest, WorkerLock
from teximport.views import ACTIVE_LIMIT_PER_USER
from textures.models import ClosetItem, Texture

pytestmark = pytest.mark.django_db


def png_bytes(size=(64, 64), color=(255, 0, 0, 255)):
    img = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _tmp_storage(tmp_path, settings):
    settings.TEXTURE_STORAGE_ROOT = str(tmp_path / "t")
    from textures import storage as sm

    sm.texture_storage._storage = FileSystemStorage(
        location=settings.TEXTURE_STORAGE_ROOT
    )
    yield
    sm.texture_storage._storage = None


@pytest.fixture()
def user():
    return User.objects.create_user(
        email="u@x.com", nickname="u", password="password8", verified=True
    )


@pytest.fixture()
def admin():
    return User.objects.create_user(
        email="adm@x.com", nickname="adm", password="password8",
        permission=User.Permission.ADMIN, verified=True,
    )


def enable_import(**opts):
    Option.objects.set("import_enabled", "1")
    for k, v in opts.items():
        Option.objects.set(k, str(v))


# ---- fetcher：URL 净化 / 解析 ----


class TestSanitizeUrl:
    def test_strips_tracking_params(self):
        url = "https://littleskin.cn/skinlib/show/929613?utm_source=x&utm_medium=y&fbclid=abc&spm=1"
        assert fetcher.sanitize_url(url) == "https://littleskin.cn/skinlib/show/929613"

    def test_keeps_legit_params(self):
        url = "https://s.com/skinlib/show?tid=87&keyword=x"
        assert fetcher.sanitize_url(url) == "https://s.com/skinlib/show?tid=87&keyword=x"

    def test_scheme_added_and_anchor_dropped(self):
        assert fetcher.sanitize_url("s.com/skinlib/show/5#top") == "https://s.com/skinlib/show/5"

    def test_invalid(self):
        assert fetcher.sanitize_url("") == ""


class TestParseTarget:
    def test_path_form(self):
        assert fetcher.parse_target("https://littleskin.cn/skinlib/show/929613") == (
            "https://littleskin.cn", 929613,
        )

    def test_query_form(self):
        assert fetcher.parse_target("https://s.com/skinlib/show?tid=87") == (
            "https://s.com", 87,
        )

    def test_invalid_no_tid(self):
        with pytest.raises(fetcher.FetchError):
            fetcher.parse_target("https://s.com/home")

    def test_invalid_scheme(self):
        with pytest.raises(fetcher.FetchError):
            fetcher.parse_target("ftp://s.com/skinlib/show/1")


class TestSSRF:
    def test_loopback_blocked(self):
        with pytest.raises(fetcher.FetchError):
            fetcher.assert_safe_host("127.0.0.1")

    def test_private_blocked(self):
        with pytest.raises(fetcher.FetchError):
            fetcher.assert_safe_host("192.168.1.1")

    def test_port_stripped_before_check(self):
        with pytest.raises(fetcher.FetchError):
            fetcher.assert_safe_host("127.0.0.1:8080")

    def test_public_host_allowed(self, monkeypatch):
        monkeypatch.setattr(
            fetcher.socket, "getaddrinfo",
            lambda host, port: [(None, None, None, "", ("93.184.216.34", 0))],
        )
        fetcher.assert_safe_host("example.com:443")


# ---- 入队（提交视图）----


def _submit(client, url):
    return client.post("/skinlib/import", {"url": url}).json()


class TestSubmit:
    def _no_dns(self, monkeypatch):
        monkeypatch.setattr(
            fetcher.socket, "getaddrinfo",
            lambda host, port: [(None, None, None, "", ("93.184.216.34", 0))],
        )
        # 提交即起 worker 会真实发网络请求/改状态，测试一律打桩
        import teximport.views as v

        monkeypatch.setattr(v, "ensure_worker", lambda: None)

    def test_disabled_by_default(self, client, user):
        client.force_login(user)
        assert _submit(client, "https://littleskin.cn/skinlib/show/1")["code"] == 1
        assert "未开放" in _submit(client, "https://littleskin.cn/skinlib/show/1")["message"]

    def test_queued_without_review(self, client, user, monkeypatch):
        self._no_dns(monkeypatch)
        enable_import()
        client.force_login(user)
        r = _submit(client, "https://littleskin.cn/skinlib/show/929613?utm_source=x")
        assert r["code"] == 0
        item = ImportRequest.objects.get()
        assert item.status == ImportRequest.Status.QUEUED
        # 追踪参数已剥（入队前完成）
        assert item.source_url == "https://littleskin.cn/skinlib/show/929613"

    def test_public_radio_persisted(self, client, user, monkeypatch):
        """单选公开 → 请求记录 requested_public=True。"""
        self._no_dns(monkeypatch)
        enable_import()
        client.force_login(user)
        resp = client.post(
            "/skinlib/import",
            {"url": "https://littleskin.cn/skinlib/show/929613", "public": "1"},
        ).json()
        assert resp["code"] == 0
        item = ImportRequest.objects.get()
        assert item.requested_public is True

    def test_default_is_private(self, client, user, monkeypatch):
        """不传参数默认私密（向后兼容旧客户端）。"""
        self._no_dns(monkeypatch)
        enable_import()
        client.force_login(user)
        _submit(client, "https://littleskin.cn/skinlib/show/929613")
        assert ImportRequest.objects.get().requested_public is False

    def test_public_flag_reaches_texture(self, user, monkeypatch):
        """worker 按请求值入库：公开请求 → 公开纹理。"""
        enable_import(score_per_storage=0, score_per_closet_item=0)
        monkeypatch.setattr(
            "teximport.worker.fetcher.fetch_texture", lambda s, t, ua: _mk_fetched(),
        )
        monkeypatch.setattr("teximport.worker.fetcher.assert_safe_host", lambda h: None)
        item = ImportRequest.objects.create(
            requester=user, source_url="https://s.com/skinlib/show/42",
            site="https://s.com", tid=42, requested_public=True,
            status=ImportRequest.Status.QUEUED,
        )
        from teximport.worker import process_one

        process_one(item.id)
        item.refresh_from_db()
        assert item.status == ImportRequest.Status.DONE
        assert item.texture.public is True

    def test_pending_with_review(self, client, user, monkeypatch):
        self._no_dns(monkeypatch)
        enable_import(import_require_review=1)
        client.force_login(user)
        r = _submit(client, "https://s.com/skinlib/show?tid=5")
        assert r["code"] == 0
        item = ImportRequest.objects.get()
        assert item.status == ImportRequest.Status.PENDING
        assert item.tid == 5 and item.site == "https://s.com"
        # 用户收到"已提交"通知
        assert Notification.objects.filter(
            recipient=user, title="导入请求已提交",
        ).exists()

    def test_invalid_url_rejected(self, client, user):
        enable_import()
        client.force_login(user)
        r = _submit(client, "https://s.com/nope")
        assert r["code"] == 1

    def test_ssrf_blocked_at_submit(self, client, user):
        enable_import()
        client.force_login(user)
        r = _submit(client, "http://127.0.0.1/skinlib/show/1")
        assert r["code"] == 1

    def test_duplicate_active_link_rejected(self, client, user, monkeypatch):
        self._no_dns(monkeypatch)
        enable_import()
        client.force_login(user)
        assert _submit(client, "https://littleskin.cn/skinlib/show/929613")["code"] == 0
        r = _submit(client, "https://littleskin.cn/skinlib/show/929613?utm=zz")
        assert r["code"] == 1 and "处理中" in r["message"]

    def test_active_limit(self, client, user, monkeypatch):
        self._no_dns(monkeypatch)
        enable_import()
        urls = [f"https://s{i}.com/skinlib/show/{i}" for i in range(2, 2 + ACTIVE_LIMIT_PER_USER)]
        client.force_login(user)
        for u in urls:
            assert _submit(client, u)["code"] == 0
        r = _submit(client, "https://other.com/skinlib/show/999")
        assert r["code"] == 1

    def test_anonymous_redirected(self, client):
        assert client.post("/skinlib/import", {"url": "x"}).status_code == 302


# ---- panel 审核 ----


class TestPanelReview:
    def _pending_item(self, user):
        return ImportRequest.objects.create(
            requester=user, source_url="https://s.com/skinlib/show/42",
            site="https://s.com", tid=42, status=ImportRequest.Status.PENDING,
        )

    def test_page_requires_admin(self, client, user):
        client.force_login(user)
        assert client.get("/admin/imports").status_code == 404

    def test_page_lists_pending(self, client, admin, user):
        client.force_login(admin)
        self._pending_item(user)
        html = client.get("/admin/imports?status=pending").content.decode()
        assert "s.com/skinlib/show/42" in html
        assert "通过" in html

    def test_page_shows_visibility_column(self, client, admin, user):
        """三个页签都含可见性列，公开/私密徽标随 requested_public。"""
        client.force_login(admin)
        ImportRequest.objects.create(
            requester=user, source_url="https://s.com/skinlib/show/1",
            site="https://s.com", tid=1, requested_public=True,
            status=ImportRequest.Status.PENDING,
        )
        ImportRequest.objects.create(
            requester=user, source_url="https://s.com/skinlib/show/2",
            site="https://s.com", tid=2, requested_public=False,
            status=ImportRequest.Status.PENDING,
        )
        html = client.get("/admin/imports?status=pending").content.decode()
        assert "可见性" in html
        assert "<span class=\"text-success\">公开</span>" in html
        assert "<span class=\"text-warning-emphasis\">私密</span>" in html

    def test_approve_queues(self, client, admin, user, monkeypatch):
        # 避免审核通过即真实起线程（ensure_worker 打桩）
        import teximport.views as v

        monkeypatch.setattr(v, "ensure_worker", lambda: None)
        item = self._pending_item(user)
        client.force_login(admin)
        r = client.post(f"/admin/imports/{item.id}/action", {"action": "approve"}).json()
        assert r["code"] == 0
        item.refresh_from_db()
        assert item.status == ImportRequest.Status.QUEUED

    def test_reject_notifies_user(self, client, admin, user):
        item = self._pending_item(user)
        client.force_login(admin)
        r = client.post(f"/admin/imports/{item.id}/action", {"action": "reject"}).json()
        assert r["code"] == 0
        item.refresh_from_db()
        assert item.status == ImportRequest.Status.REJECTED
        assert Notification.objects.filter(
            recipient=user, title="导入请求被拒绝",
        ).exists()

    def test_double_action_rejected(self, client, admin, user):
        item = self._pending_item(user)
        client.force_login(admin)
        assert client.post(
            f"/admin/imports/{item.id}/action", {"action": "approve"}
        ).json()["code"] == 0
        r = client.post(f"/admin/imports/{item.id}/action", {"action": "approve"}).json()
        assert r["code"] == 1


# ---- 拉取处理（process_one，不走线程）----


def _mk_fetched(name="导入皮", ttype="steve", size_kb=1):
    return {"name": name, "type": ttype, "size_kb": size_kb, "png": png_bytes()}


def _queued_item(user, url="https://s.com/skinlib/show/42"):
    return ImportRequest.objects.create(
        requester=user, source_url=url, site="https://s.com", tid=42,
        status=ImportRequest.Status.QUEUED,
    )


class TestProcessOne:
    def _patch_fetch(self, monkeypatch, result=None, error=None):
        if error is not None:
            def _fail(site, tid, ua):
                raise error
            monkeypatch.setattr(
                "teximport.worker.fetcher.fetch_texture", _fail
            )
        else:
            monkeypatch.setattr(
                "teximport.worker.fetcher.fetch_texture",
                lambda site, tid, ua: result or _mk_fetched(),
            )
        monkeypatch.setattr("teximport.worker.fetcher.assert_safe_host", lambda h: None)

    def test_success_creates_private_texture_with_source(self, user, monkeypatch):
        self._patch_fetch(monkeypatch)
        enable_import(
            import_score_multiplier=3, score_per_storage=1, score_per_closet_item=2,
        )
        user.score = 100
        user.save()
        item = _queued_item(user)
        from teximport.worker import process_one

        process_one(item.id)
        item.refresh_from_db()
        assert item.status == ImportRequest.Status.DONE
        texture = item.texture
        assert texture.type == "steve"
        assert texture.public is False
        assert texture.source_url == "https://s.com/skinlib/show/42"
        assert texture.uploader == user
        # 计费：(1*1 + 2) * 3 = 9
        assert item.score_cost == 9
        user.refresh_from_db()
        assert user.score == 91
        # 已加入衣柜
        assert ClosetItem.objects.filter(owner=user, texture=texture).exists()
        # 通知
        assert Notification.objects.filter(
            recipient=user, title="纹理导入成功",
        ).exists()

    def test_dedup_public_existing(self, user, monkeypatch):
        self._patch_fetch(monkeypatch)
        ImportRequest.objects.all().delete()
        from teximport.worker import process_one
        from textures.validation import validate_texture_image

        # 已有纹理须与 fixture PNG 的真实像素 hash 一致才会命中去重
        digest = validate_texture_image(png_bytes(), "steve").digest
        existing = Texture.objects.create(
            name="已有", type="steve", hash=digest, size=1,
            uploader=user, public=True,
        )
        item = _queued_item(user)
        process_one(item.id)
        item.refresh_from_db()
        assert item.status == ImportRequest.Status.DEDUP
        assert item.texture == existing
        # 不重复建、不扣分
        assert Texture.objects.count() == 1
        assert Notification.objects.filter(title="纹理导入完成").exists()

    def test_dedup_private_of_other_fails(self, user, monkeypatch):
        self._patch_fetch(monkeypatch)
        other = User.objects.create_user(
            email="o@x.com", nickname="o", password="password8"
        )
        from textures.validation import validate_texture_image

        digest = validate_texture_image(png_bytes(), "steve").digest
        Texture.objects.create(
            name="他人私皮", type="steve", hash=digest, size=1,
            uploader=other, public=False,
        )
        item = _queued_item(user)
        from teximport.worker import process_one

        process_one(item.id)
        item.refresh_from_db()
        assert item.status == ImportRequest.Status.FAILED

    def test_insufficient_score(self, user, monkeypatch):
        self._patch_fetch(monkeypatch)
        enable_import(
            import_score_multiplier=100, score_per_storage=10, score_per_closet_item=0,
        )
        user.score = 5
        user.save()
        item = _queued_item(user)
        from teximport.worker import process_one

        process_one(item.id)
        item.refresh_from_db()
        assert item.status == ImportRequest.Status.FAILED
        assert "积分不足" in item.error_text
        user.refresh_from_db()
        assert user.score == 5  # 未扣分
        assert Notification.objects.filter(title="纹理导入失败").exists()

    def test_fetch_error_marks_failed(self, user, monkeypatch):
        self._patch_fetch(monkeypatch, error=fetcher.FetchError("来源站返回纹理不存在或非公开"))
        item = _queued_item(user)
        from teximport.worker import process_one

        process_one(item.id)
        item.refresh_from_db()
        assert item.status == ImportRequest.Status.FAILED
        assert "不存在" in item.error_text

    def test_only_claims_queued(self, user, monkeypatch):
        """processing/失败态的项不会被重复认领。"""
        self._patch_fetch(monkeypatch)
        from teximport.worker import process_one

        item = _queued_item(user)
        ImportRequest.objects.filter(id=item.id).update(status=ImportRequest.Status.PROCESSING)
        process_one(item.id)  # 无变化而非报错
        item.refresh_from_db()
        assert item.status == ImportRequest.Status.PROCESSING


# ---- WorkerLock 互斥 ----


class TestWorkerLock:
    def test_acquire_release(self):
        assert WorkerLock.acquire("A", 60)
        assert not WorkerLock.acquire("B", 60)  # 被持有
        WorkerLock.release("A")
        assert WorkerLock.acquire("B", 60)

    def test_wrong_holder_cannot_release(self):
        WorkerLock.acquire("A", 60)
        WorkerLock.release("X")
        assert not WorkerLock.acquire("B", 60)

    def test_expired_lock_reacquirable(self):
        WorkerLock.acquire("A", -10)  # TTL 已过期
        assert WorkerLock.acquire("B", 60)


# ---- worker 循环与线程（SQLite 内存库不支持跨线程连接，循环同步测）----


class TestWorkerLoop:
    def test_loop_processes_queue_then_exits(self, user, monkeypatch):
        """同步跑 _worker_loop：处理完队列后应自然返回（不依赖线程）。"""
        enable_import(import_interval_seconds=1, score_per_storage=0,
                      score_per_closet_item=0)
        monkeypatch.setattr(
            "teximport.worker.fetcher.fetch_texture", lambda s, t, ua: _mk_fetched(),
        )
        monkeypatch.setattr("teximport.worker.fetcher.assert_safe_host", lambda h: None)
        monkeypatch.setattr("teximport.worker.time.sleep", lambda s: None)
        item = _queued_item(user)
        from teximport import worker as w

        w._worker_loop()  # 同步执行一轮完整循环

        item.refresh_from_db()
        assert item.status == ImportRequest.Status.DONE
        assert Notification.objects.filter(title="纹理导入成功").exists()

    def test_loop_skips_when_disabled(self, user, monkeypatch):
        monkeypatch.setattr(
            "teximport.worker.time.sleep", lambda s: None,
        )
        _queued_item(user)  # 队列有货也直接退出
        from teximport import worker as w

        w._worker_loop()  # 未启用：立即返回
        item = ImportRequest.objects.get()
        assert item.status == ImportRequest.Status.QUEUED

    def test_ensure_worker_spawns_thread(self, monkeypatch):
        import threading

        from teximport import worker as w

        enable_import()
        called = threading.Event()
        monkeypatch.setattr(w, "_worker_loop", lambda: called.set())
        w._thread_running = False
        w.ensure_worker()
        assert called.wait(timeout=5) is True
        # 桩不含真实循环的 finally 复位，手动清理供后续用例
        w._thread_running = False

    def test_ensure_worker_twice_single_thread(self, monkeypatch):
        """线程已运行时重复 ensure 不再起新线程（计数闭合）。"""
        import threading

        from teximport import worker as w

        enable_import()
        gate = threading.Event()
        calls = []

        def slow_loop():
            calls.append(1)
            gate.wait(timeout=5)  # 阻塞模拟长任务

        monkeypatch.setattr(w, "_worker_loop", slow_loop)
        w._thread_running = False
        w.ensure_worker()
        w.ensure_worker()  # 第二次应直接返回
        gate.set()
        import time as _t

        for _ in range(50):
            if len(calls) >= 1 and w._thread_running is True:
                break
            _t.sleep(0.02)
        assert len(calls) == 1
        w._thread_running = False

    def test_ensure_worker_noop_when_disabled(self):
        from teximport import worker as w

        w._thread_running = False
        w.ensure_worker()  # 未启用：不起线程、不报错
        assert w._thread_running is False


# ---- 深度安全：逐跳重定向 SSRF / 流式体积上限 / 自站拦截 ----


class _FakeResp:
    """requests.Response 的最小替身（stream 语义）。"""

    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size=65536):
        view = memoryview(self._body)
        for i in range(0, len(view), chunk_size):
            yield bytes(view[i:i + chunk_size])

    def json(self):
        import json as _j

        return _j.loads(self._body)

    def close(self):
        self.closed = True


class TestSecurityRedirectChain:
    @staticmethod
    def _dns(monkeypatch):
        """真实语义的 DNS 桩：IP 字面量原样返回（getaddrinfo 的真实行为），
        公网域名解析到假公网 IP——避免桩把 SSRF 校验本身骗过。"""

        def fake_getaddrinfo(host, port):
            try:
                ipaddress.ip_address(host)
                addrs = [host]
            except ValueError:
                addrs = ["93.184.216.34"]
            return [(None, None, None, "", (a, 0)) for a in addrs]

        monkeypatch.setattr(fetcher.socket, "getaddrinfo", fake_getaddrinfo)

    def _run_get(self, monkeypatch, responses, url="https://pub.example.com/x"):
        """responses: URL 后缀映射 → _FakeResp。"""
        seen = []

        def fake_get(u, timeout, headers, allow_redirects, stream):
            seen.append(u)
            for suffix, resp in responses.items():
                if u.endswith(suffix):
                    return resp
            raise AssertionError(f"unexpected url {u}")

        monkeypatch.setattr(fetcher.http_client, "get", fake_get)
        return fetcher._get(url, "UA", "测试")

    def test_redirect_to_loopback_blocked(self, monkeypatch):
        """公网站点 302 → 127.0.0.1：第二跳必须被 assert_safe_host 拦截。"""
        self._dns(monkeypatch)
        responses = {
            "/first": _FakeResp(status=302, headers={"Location": "http://127.0.0.1/secret"}),
        }
        with pytest.raises(fetcher.FetchError, match="内网|本机"):
            self._run_get(monkeypatch, responses, url="https://pub.example.com/first")

    def test_redirect_to_metadata_blocked(self, monkeypatch):
        """302 → 云元数据 169.254.169.254 同样被拦（真实漏洞回归）。"""
        self._dns(monkeypatch)
        responses = {
            "/first": _FakeResp(
                status=302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
            ),
        }
        with pytest.raises(fetcher.FetchError):
            self._run_get(monkeypatch, responses, url="https://pub.example.com/first")

    def test_multiple_hops_ok_then_bad_blocked(self, monkeypatch):
        """公网→公网跳转放行，第三跳转内网（10.x）被拦。"""
        self._dns(monkeypatch)
        responses = {
            "/a": _FakeResp(status=302, headers={"Location": "https://pub.example.com/b"}),
            "/b": _FakeResp(status=302, headers={"Location": "http://10.0.0.5/x"}),
        }
        with pytest.raises(fetcher.FetchError, match="内网|本机"):
            self._run_get(monkeypatch, responses, url="https://pub.example.com/a")

    def test_redirect_loop_capped(self, monkeypatch):
        self._dns(monkeypatch)
        responses = {
            "/loop": _FakeResp(status=302, headers={"Location": "https://pub.example.com/loop"}),
        }
        with pytest.raises(fetcher.FetchError, match="重定向次数"):
            self._run_get(monkeypatch, responses, url="https://pub.example.com/loop")


class TestSecurityStreamCap:
    def test_content_length_declared_over(self):
        resp = _FakeResp(headers={"Content-Length": str(9 * 1024 * 1024)})
        with pytest.raises(fetcher.FetchError, match="上限"):
            fetcher._read_capped(resp)

    def test_chunked_body_over_cap(self):
        body = b"x" * (fetcher.MAX_DOWNLOAD_BYTES + 1)
        resp = _FakeResp(body=body)
        with pytest.raises(fetcher.FetchError, match="上限"):
            fetcher._read_capped(resp)
        assert resp.closed  # 超限即断开

    def test_normal_body_passes(self):
        body = b"y" * 1024
        assert fetcher._read_capped(_FakeResp(body=body)) == body


class TestSecuritySelfImport:
    def _allow_dns(self, monkeypatch):
        monkeypatch.setattr(
            fetcher.socket, "getaddrinfo",
            lambda host, port: [(None, None, None, "", ("93.184.216.34", 0))],
        )

    def test_same_host_rejected(self, client, user, monkeypatch):
        self._allow_dns(monkeypatch)
        enable_import()
        client.force_login(user)
        # 测试客户端 host = testserver
        r = client.post(
            "/skinlib/import", {"url": "https://testserver/skinlib/show/1"}
        ).json()
        assert r["code"] == 1
        assert "本站" in r["message"]

    def test_site_url_host_rejected_too(self, client, user, monkeypatch):
        self._allow_dns(monkeypatch)
        enable_import()
        Option.objects.set("site_url", "https://skin.dreamweaver.top")
        client.force_login(user)
        r = client.post(
            "/skinlib/import",
            {"url": "https://skin.dreamweaver.top/skinlib/show/87"},
        ).json()
        assert r["code"] == 1
        assert "本站" in r["message"]

    def test_external_host_still_accepted(self, client, user, monkeypatch):
        self._allow_dns(monkeypatch)
        enable_import()
        client.force_login(user)
        r = client.post(
            "/skinlib/import", {"url": "https://littleskin.cn/skinlib/show/929613"}
        ).json()
        assert r["code"] == 0


# ---- 页面渲染 ----


class TestTemplates:
    def test_upload_page_icon_when_enabled(self, client, user):
        enable_import(import_score_multiplier=5)
        client.force_login(user)
        html = client.get("/skinlib/upload").content.decode()
        assert "从其他皮肤站导入" in html
        assert "import-modal" in html
        assert "×5" in html

    def test_import_modal_has_visibility_radio(self, client, user):
        """模态框含可见性单选（默认私密 + 公开选项 + JS 提交 public）。"""
        enable_import()
        client.force_login(user)
        html = client.get("/skinlib/upload").content.decode()
        assert 'name="import-public"' in html
        assert 'value="0" checked' in html  # 默认私密
        assert 'id="import-public"' in html
        assert "导入后可见性" in html
        assert 'fd.set("public"' in html  # JS 提交携带

    def test_upload_page_hidden_when_disabled(self, client, user):
        client.force_login(user)
        html = client.get("/skinlib/upload").content.decode()
        assert "从其他皮肤站导入" not in html

    def test_options_page_has_import_section(self, client, db):
        from accounts.models import User as U

        super_u = U.objects.create_user(
            email="s@x.com", nickname="s", password="password8",
            permission=User.Permission.SUPER_ADMIN, verified=True,
        )
        client.force_login(super_u)
        html = client.get("/admin/options/import").content.decode()
        assert "import_enabled" in html
        assert "User-Agent 池" in html

    def test_show_page_renders_source_link(self, client, user):
        t = Texture.objects.create(
            name="导入皮", type="steve", hash="c" * 64, size=1,
            uploader=user, public=True, source_url="https://s.com/skinlib/show/42",
        )
        client.force_login(user)
        html = client.get(f"/skinlib/show?tid={t.tid}").content.decode()
        assert "https://s.com/skinlib/show/42" in html
        assert "来源" in html
