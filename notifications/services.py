"""站内通知发件助手：各业务点统一落库入口。

对照原版 Notification::create 的语义（收件人 + 标题 + 内容）。
通知失败不应影响主流程——调用方均可直接调用（落库失败按需吞掉异常）。
"""

from .models import Notification


def notify(recipient, title: str, content: str = "") -> Notification:
    return Notification.objects.create(
        recipient=recipient, title=title, content=content
    )
