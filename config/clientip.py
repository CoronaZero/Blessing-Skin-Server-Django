"""客户端 IP 提取（全站唯一实现，accounts/yggdrasil/oauth_bridge 共用）。

取值策略：X-Forwarded-For 的**末跳**（由离应用最近的一层可信反代追加），
无 XFF 回落 REMOTE_ADDR。

安全语义：反代（Nginx/OpenResty 的 $proxy_add_x_forwarded_for）对 XFF 是
追加而非覆盖语义，客户端可自带伪造 XFF 首段——取首跳意味着任何限流、
注册上限、审计 IP 都可被请求头任意伪造绕过；末跳由自家反代写入，
客户端不可控。多层代理（如前置 CDN）场景应改用 django-ipware 按可信
代理深度取值。
"""


def get_client_ip(request) -> str:
    """客户端 IP：XFF 末跳优先，无 XFF 回落 REMOTE_ADDR。"""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.META.get("REMOTE_ADDR", "")
