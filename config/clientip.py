"""客户端 IP 提取（全站唯一实现，accounts/yggdrasil/oauth_bridge 共用）。

取值策略：X-Forwarded-For 的**右起第 CLIENT_IP_PROXY_DEPTH 跳**。
XFF 为追加语义：每层自有可信代理（反代/CDN）把「直接对端 IP」追加到
末尾——从右往左数，第 N 层可信代理写入的位置即真实客户端 IP：

- 单层反代（默认 depth=1）：末跳 = 反代看到的客户端 IP；
- 前置 CDN（depth=2）：末跳 = 反代看到的 CDN 节点 IP，右起第 2 跳
  = CDN 看到的客户端 IP（直连 CDN 的伪造段被剥离）。

安全语义：XFF 左侧段为客户端自带可伪造值，永远不可采信；层数是唯一
需要按部署拓扑维护的假设——宁可少配（取到代理 IP）不可多配（多配会
采信客户端可伪造段）。XFF 跳数不足（代理未按预期追加）回落末跳，
无 XFF 回落 REMOTE_ADDR。层数经环境变量 CLIENT_IP_PROXY_DEPTH 配置
（默认 1）。
"""


def _trusted_depth() -> int:
    from django.conf import settings

    try:
        return max(1, int(getattr(settings, "CLIENT_IP_PROXY_DEPTH", 1)))
    except (TypeError, ValueError):
        return 1


def get_client_ip(request) -> str:
    """客户端 IP：XFF 右起第 N 跳（N=CLIENT_IP_PROXY_DEPTH），
    跳数不足回落末跳，无 XFF 回落 REMOTE_ADDR。"""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            depth = _trusted_depth()
            if depth > 1 and len(parts) >= depth:
                return parts[-depth]
            return parts[-1]
    return request.META.get("REMOTE_ADDR", "")
