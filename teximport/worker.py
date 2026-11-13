"""导入队列 worker：全局单线程拉取（间隔可配、随机 UA、完成后通知）。

约束（对照需求）：
- 全站同时仅允许一个线程拉取 → WorkerLock DB 互斥（多 gunicorn 进程安全）
- 每两个皮肤之间的拉取间隔 → import_interval_seconds 选项
- UA 从 import_user_agents 池随机选取（一行一个；留空用默认）
- 结束（成功/失败/已存在）后站内通知请求者
"""

import random
import threading
import time
import uuid

from options.models import Option

from . import fetcher
from .models import ImportRequest, WorkerLock

DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_MULTIPLIER = 3

_worker_lock = threading.Lock()  # 进程内防重复起线程
_thread_running = False


def pick_user_agent() -> str:
    """从管理员配置的 UA 池随机选取（一行一个；留空回落默认）。"""
    pool = [
        line.strip() for line in (Option.objects.get_value("import_user_agents") or "").splitlines()
        if line.strip()
    ]
    if not pool:
        return fetcher.DEFAULT_USER_AGENT
    return random.choice(pool)  # noqa: S311 - 随机选取非安全场景


def interval_seconds() -> int:
    try:
        value = int(Option.objects.get_value("import_interval_seconds") or DEFAULT_INTERVAL_SECONDS)
    except ValueError:
        value = DEFAULT_INTERVAL_SECONDS
    return max(1, value)


def score_multiplier() -> int:
    try:
        value = int(Option.objects.get_value("import_score_multiplier") or DEFAULT_MULTIPLIER)
    except ValueError:
        value = DEFAULT_MULTIPLIER
    return max(1, value)


def ensure_worker() -> None:
    """确保后台 worker 线程在跑（进程内去重 + DB 锁跨进程互斥）。"""
    global _thread_running
    if not _enabled():
        return
    with _worker_lock:
        if _thread_running:
            return
        _thread_running = True
    thread = threading.Thread(target=_worker_loop, daemon=True, name="teximport-worker")
    thread.start()


def _enabled() -> bool:
    raw = (Option.objects.get_value("import_enabled") or "0").strip().lower()
    return raw in ("1", "true", "on")


# ---- 处理单件（可被测试直接调用，不依赖线程）----


def _notify(user, title: str, content: str) -> None:
    from notifications.models import Notification

    Notification.objects.create(recipient=user, title=title, content=content)


def _calc_cost(size_kb: int, multiplier: int) -> int:
    """计费：(每 KB 存储费 + 收藏费) × 倍率（无上传奖励）。"""
    try:
        per_kb = int(
            Option.objects.get_value("score_per_storage")
            or Option.objects.get_value("private_score_per_storage")
            or 0
        )
    except ValueError:
        per_kb = 0
    try:
        per_closet = int(Option.objects.get_value("score_per_closet_item") or 0)
    except ValueError:
        per_closet = 0
    return (size_kb * per_kb + per_closet) * multiplier


def process_one(request_id: int) -> None:
    """拉取一件：info → PNG → 校验 → 计分/去重 → 建纹理/衣柜 → 通知。

    状态机 queued → processing → done/failed/dedup（乐观抢占，非本线程抢到则跳过）。
    """
    from django.db import close_old_connections

    close_old_connections()  # 长睡眠后旧连接可能失效

    claimed = ImportRequest.objects.filter(
        id=request_id, status=ImportRequest.Status.QUEUED
    ).update(status=ImportRequest.Status.PROCESSING)
    if not claimed:
        return
    item = ImportRequest.objects.select_related("requester").get(id=request_id)

    try:
        site, tid = fetcher.parse_target(item.source_url)
    except fetcher.FetchError as exc:
        _finish_failed(item, str(exc))
        return

    user_agent = pick_user_agent()
    try:
        fetcher.assert_safe_host(urlsplit_host(site))
        fetched = fetcher.fetch_texture(site, tid, user_agent)
    except fetcher.FetchError as exc:
        _finish_failed(item, str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - 网络/解析等全部异常归为失败
        _finish_failed(item, f"拉取异常：{exc}")
        return

    from textures.models import ClosetItem, Texture
    from textures.storage import texture_storage
    from textures.validation import validate_texture_image

    result = validate_texture_image(fetched["png"], fetched["type"])
    if not result.valid:
        _finish_failed(item, f"来源纹理未通过校验：{result.error}")
        return

    # 去重：hash 全局唯一
    existing = Texture.objects.filter(hash=result.digest).first()
    if existing is not None:
        if existing.public or existing.uploader_id == item.requester_id:
            item.status = ImportRequest.Status.DEDUP
            item.texture = existing
            item.error_text = ""
            item.processed_at = _now()
            item.save(update_fields=["status", "texture", "error_text", "processed_at"])
            _notify(
                item.requester, "纹理导入完成",
                f"来源站的「{fetched['name']}」站内已存在（#{existing.tid}），"
                "未重复创建，也未扣积分。",
            )
            return
        _finish_failed(item, "站内已存在相同纹理且不可用，无法导入")
        return

    # 计费（对照上传视图口径 × 管理员倍率）
    multiplier = score_multiplier()
    cost = _calc_cost(fetched["size_kb"], multiplier)
    user = item.requester
    if cost > user.score:
        _finish_failed(item, f"积分不足（需 {cost}，当前 {user.score}）")
        return
    if cost:
        from accounts.models import User

        User.objects.filter(uid=user.uid).update(score=user.score - cost)

    texture = Texture.objects.create(
        name=fetched["name"][:50],
        type=fetched["type"],
        hash=result.digest,
        size=fetched["size_kb"],
        uploader=user,
        public=False,  # 导入默认私密
        source_url=item.source_url,
    )
    if not texture_storage.exists(result.digest):
        texture_storage.save_bytes(result.digest, result.normalized)
    # 入请求者衣柜（费用已含在倍率计费中）
    ClosetItem.objects.get_or_create(
        owner=user, texture=texture, defaults={"item_name": fetched["name"][:255]}
    )

    item.status = ImportRequest.Status.DONE
    item.texture = texture
    item.score_cost = cost
    item.processed_at = _now()
    item.save(update_fields=["status", "texture", "score_cost", "processed_at"])
    _notify(
        item.requester, "纹理导入成功",
        f"「{texture.name}」已导入并加入你的衣柜（消耗 {cost} 积分），"
        f"查看：/skinlib/show?tid={texture.tid}",
    )


def _finish_failed(item: ImportRequest, reason: str) -> None:
    item.status = ImportRequest.Status.FAILED
    item.error_text = reason[:255]
    item.processed_at = _now()
    item.save(update_fields=["status", "error_text", "processed_at"])
    _notify(item.requester, "纹理导入失败", f"「{item.source_url}」导入失败：{reason}")


def _now():
    from django.utils import timezone

    return timezone.now()


def urlsplit_host(site: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(site).netloc


# ---- 后台线程循环 ----


def _worker_loop() -> None:
    """循环认领 queued 项逐件处理，件间睡眠可配间隔；无任务或抢不到锁则退出。

    下次入队/审核通过时会由 ensure_worker() 重新唤起。
    """
    global _thread_running
    holder = uuid.uuid4().hex
    try:
        while True:
            if not _enabled():
                return
            # 以「间隔×2」为锁 TTL 抢占（处理期间单件远小于该窗口）
            if not WorkerLock.acquire(holder, ttl_seconds=interval_seconds() * 2 + 120):
                time.sleep(max(1, interval_seconds()))
                continue
            try:
                item = ImportRequest.objects.filter(
                    status=ImportRequest.Status.QUEUED
                ).order_by("id").values_list("id", flat=True).first()
                if item is None:
                    return  # 队列空：退出，等下次 ensure_worker
                try:
                    process_one(item)
                finally:
                    # 拉取间隔（礼貌爬取）：件间等待，放在锁内串行执行
                    time.sleep(max(0, interval_seconds()))
            finally:
                WorkerLock.release(holder)
    except Exception:  # noqa: BLE001 - 后台线程绝不能向上抛
        import logging

        logging.getLogger("teximport").exception("import worker crashed")
    finally:
        with _worker_lock:
            _thread_running = False
