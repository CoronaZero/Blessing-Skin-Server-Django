"""JSON 契约助手：对照原版 json() 助手（{code, message, data}）。"""

from django.http import JsonResponse

# 语义对照原版：0 成功 / 1 常规错误 / 2 特殊状态（原样用于前端分支判断）
SUCCESS = 0
FAILURE = 1
SPECIAL = 2


def json_response(message, code=FAILURE, data=None, status=200):
    payload = {"code": code, "message": str(message)}
    if data is not None:
        payload["data"] = data
    return JsonResponse(payload, status=status)
