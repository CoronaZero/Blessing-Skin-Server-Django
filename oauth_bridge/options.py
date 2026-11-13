"""oauth_bridge 选项读取：开关组合与类型解析（面板配置的运行时消费端）。"""

from options.models import Option


def _opt(name: str, default: str = "") -> str:
    return str(Option.objects.get_value(name, default) or default)


def _flag(name: str) -> bool:
    """布尔选项判定（项目惯用：strip().lower() in 1/true/on）。"""
    return _opt(name).strip().lower() in ("1", "true", "on")


def _int(name: str, default: int = 0) -> int:
    try:
        return int(_opt(name).strip() or default)
    except ValueError:
        return default


def client_id() -> str:
    return _opt("microsoft_client_id").strip()


def client_secret() -> str:
    return _opt("microsoft_client_secret").strip()


def msa_configured() -> bool:
    """凭证齐全（未启用也可先行配置）。"""
    return bool(client_id() and client_secret())


def msa_login_ready() -> bool:
    """登录入口可用：总开关 + 登录开关 + 凭证齐全。"""
    return _flag("microsoft_oauth_enabled") and _flag("microsoft_login_enabled") \
        and msa_configured()


def msa_register_ready() -> bool:
    """注册入口可用（登录页/注册页按钮展示与建号放行共用）。"""
    return msa_login_ready() and _flag("microsoft_register_enabled")


def msa_bind_ready() -> bool:
    """正版角色绑定入口可用。"""
    return _flag("microsoft_oauth_enabled") and _flag("microsoft_bind_enabled") \
        and msa_configured()


def passthrough_ready() -> bool:
    """正版验证透传可用：透传独立开关 + 绑定开关（透传依赖绑定关系存在）。"""
    return _flag("microsoft_passthrough_enabled") and msa_bind_ready()


def unbind_cooldown_days() -> int:
    """解绑冷却天数（0 = 不冷却）。"""
    return max(0, _int("microsoft_unbind_cooldown_days"))


def outbound_timeout_seconds(default: int = 10) -> int:
    """上游请求超时（秒）；透传路径用更短值单独传参。"""
    return default
