"""选项注册表：类型化控件 + 分组定义（自研管理面板的选项心脏）。

对照原版 OptionForm 的分组表单：每项声明控件类型，
面板按注册表自动渲染开关/文本/数字/密钥控件。
"""

from dataclasses import dataclass, field


@dataclass
class OptionField:
    key: str
    label: str
    widget: str = "text"  # text / bool / int / textarea / secret_textarea / password / select
    help_text: str = ""
    choices: list = field(default_factory=list)  # select 用
    rows: int = 2  # textarea 行数
    monospace: bool = False  # textarea 等宽字体（密钥/正则等）


@dataclass
class OptionGroup:
    slug: str
    title: str
    fields: list
    super_admin_only: bool = False
    section: str = "general"  # general / users / services / yggdrasil


# 子页签定义（拆分后的选项导航）
OPTION_SECTIONS = [
    ("general", "常规", "基本信息、默认材质、首页背景"),
    ("users", "用户与注册", "注册、角色名规则、积分、登录安全"),
    ("services", "对接服务", "邮件 SMTP、验证码"),
    ("yggdrasil", "Yggdrasil", "认证协议与签名密钥"),
    ("import", "皮肤导入", "站外纹理拉取：开关、间隔、UA、倍率、审核"),
]


OPTION_REGISTRY: list[OptionGroup] = [
    OptionGroup(
        slug="general",
        title="基本信息",
        section="general",
        fields=[
            OptionField("site_name", "站点名称"),
            OptionField("site_url", "站点地址", help_text="https://skin.example.com，邮件链接用它构建"),
            OptionField("site_description", "站点描述", widget="textarea"),
            OptionField(
                "site_announcement", "站点公告（Markdown）", widget="textarea",
                help_text="显示在用户中心仪表盘与公告页，支持 Markdown 语法",
            ),
            OptionField(
                "home_bg_url", "首页背景图链接", widget="textarea", rows=2,
                help_text="填写图片 URL（如 https://cdn.example.com/bg.jpg），"
                          "铺满首页整页（cover + 滚动固定），hero 区自动叠深色遮罩保证白字可读，"
                          "留空则使用纯色背景；请在图片主题下检查文字遮罩效果",
            ),
        ],
    ),
    OptionGroup(
        slug="defaults",
        title="默认材质",
        super_admin_only=True,
        fields=[
            OptionField(
                "default_skin_tid", "默认皮肤 TID",
                widget="int",
                help_text="用户创建角色时自动应用；无皮肤角色的预览用它占位（0=停用）",
            ),
            OptionField(
                "default_cape_tid", "默认披风 TID",
                widget="int",
                help_text="披风演示占位与创建角色默认应用（0=停用）",
            ),
        ],
    ),
OptionGroup(
        slug="register",
        title="注册",
        section="users",
        fields=[
            OptionField("disable_registration", "关闭注册", widget="bool"),
            OptionField("invitation_required", "注册需邀请码", widget="bool"),
            OptionField("require_verification", "注册需邮箱验证", widget="bool"),
            OptionField("regs_per_ip", "同 IP 注册上限", widget="int"),
            OptionField("player_limit_per_user", "每人角色上限（0=不限）", widget="int"),
        ],
    ),
    OptionGroup(
        slug="unverified",
        title="未验证用户限制",
        section="users",
        fields=[
            OptionField(
                "unverified_block_skinlib", "禁止浏览皮肤库", widget="bool",
                help_text="勾选后未验证邮箱的用户无法访问皮肤库列表与材质详情页",
            ),
            OptionField(
                "unverified_block_upload", "禁止上传皮肤", widget="bool",
                help_text="上传页与上传接口一并禁止；与「禁止浏览皮肤库」独立生效",
            ),
            OptionField(
                "unverified_block_ygg", "禁止使用 Yggdrasil API", widget="bool",
                help_text="未验证邮箱的账号无法通过启动器登录（authenticate 返回 403）",
            ),
            OptionField(
                "unverified_block_import", "禁止站外导入皮肤", widget="bool",
                help_text="未验证邮箱的用户无法提交站外皮肤导入请求",
            ),
        ],
    ),
    OptionGroup(
        slug="friends",
        title="好友系统",
        section="users",
        fields=[
            OptionField(
                "friends_enabled", "启用好友系统", widget="bool",
                help_text="开启后在 API 元数据声明 enable_mojang_anti_features，"
                          "Minecraft 26.2+ 客户端好友列表/在线状态可用；"
                          "开启前请确认本站已实现全部 minecraftservices 端点",
            ),
            OptionField(
                "friends_rate_limit", "好友端点每分钟上限（每用户）", widget="int",
                help_text="GET/PUT /friends 的兜底节流，超出返回 429 + Retry-After；0=不限",
            ),
        ],
    ),
    OptionGroup(
        slug="score",
        title="积分",
        section="users",
        fields=[
            OptionField("score_per_storage", "公开纹理每 KB 扣分", widget="int"),
            OptionField("private_score_per_storage", "私密纹理每 KB 扣分", widget="int"),
            OptionField("score_per_closet_item", "收藏扣分", widget="int"),
            OptionField("score_per_sign", "签到得分", widget="int"),
            OptionField("score_award_per_texture", "上传奖励（抵扣）", widget="int"),
            OptionField("user_initial_score", "新用户初始积分", widget="int"),
        ],
    ),
    OptionGroup(
        slug="skinlib_upload",
        title="皮肤库上传限制",
        section="users",
        fields=[
            OptionField(
                "max_upload_file_size", "站内上传体积上限（KB）", widget="int",
                help_text="上传页/上传接口按原始文件字节执行（对照原版，默认 2048KB）；"
                          "Yggdrasil API 路径维持独立 4MiB 上限",
            ),
            OptionField(
                "max_texture_width", "纹理像素尺寸上限", widget="int",
                help_text="宽/高均不得超过（默认 8192；渲染预览另有像素总量保护，"
                          "超限纹理仍可下载但不再生成头像/立绘）",
            ),
        ],
    ),
    OptionGroup(
        slug="player_name",
        title="角色名规则",
        section="users",
        fields=[
            OptionField(
                "player_name_rule", "角色名规则", widget="select",
                choices=[("official", "官方（字母数字下划线）"), ("cjk", "允许中文"),
                         ("utf8", "UTF-8 任意"), ("custom", "自定义正则")],
            ),
            OptionField("custom_player_name_regexp", "自定义正则（规则=custom 时）"),
            OptionField("player_name_length_min", "最小长度", widget="int"),
            OptionField("player_name_length_max", "最大长度", widget="int"),
        ],
    ),
    OptionGroup(
        slug="login",
        title="登录与安全",
        section="users",
        super_admin_only=True,
        fields=[
            OptionField("login_enumeration_protection",
                        "登录枚举防护（统一报错文案）", widget="bool"),
            OptionField("score_forget_password", "找回密码扣分", widget="int"),
        ],
    ),
    OptionGroup(
        slug="microsoft",
        title="Microsoft OAuth",
        super_admin_only=True,
        section="services",
        fields=[
            OptionField(
                "microsoft_oauth_enabled", "启用 Microsoft OAuth", widget="bool",
                help_text="总开关；应用注册请前往 Azure Portal（aka.ms/aad），"
                          "平台类型选 Web，重定向 URI 填本站回调地址",
            ),
            OptionField("microsoft_client_id", "应用 ID（client_id）"),
            OptionField("microsoft_client_secret", "应用密码（client_secret）",
                        widget="password", help_text="留空保存不修改"),
            OptionField("microsoft_login_enabled", "允许用于账号登录", widget="bool",
                        help_text="登录/注册页显示 Microsoft 按钮；已登录用户访问时转为绑定入口"),
            OptionField(
                "microsoft_register_enabled", "允许用于注册新账号", widget="bool",
                help_text="未绑定的 Microsoft 账号在引导页可选择创建新账号；"
                          "创建时仍受注册开关、邀请码、同 IP 上限等全部注册限制",
            ),
            OptionField(
                "microsoft_bind_enabled", "允许绑定正版角色", widget="bool",
                help_text="用户可在「我的角色」页发起 Xbox 链路验证"
                          "并绑定正版角色（只读，不建本地角色）",
            ),
            OptionField(
                "microsoft_passthrough_enabled", "正版验证透传", widget="bool",
                help_text="开启后，已绑定正版角色的玩家可用正版客户端进入指向本站的 "
                          "authlib-injector 服务器（hasJoined 转发 Mojang 校验；上游失败一律拒绝）",
            ),
            OptionField(
                "microsoft_unbind_cooldown_days", "解绑冷却（天）", widget="int",
                help_text="解绑 Microsoft 账号/正版角色后，多少天内不可重新绑定；0=不冷却",
            ),
            OptionField(
                "microsoft_outbound_proxy", "出站代理地址",
                help_text="访问 Microsoft/Mojang API 的代理（http(s)://IP:端口），留空直连；"
                          "socks 代理请转换为 http 端口后填写",
            ),
        ],
    ),
    OptionGroup(
        slug="captcha",
        title="验证码",
        super_admin_only=True,
        section="services",
        fields=[
            OptionField(
                "captcha_provider", "验证码提供者", widget="select",
                choices=[("", "关闭"), ("turnstile", "Cloudflare Turnstile"),
                         ("recaptcha", "Google reCAPTCHA")],
            ),
            OptionField("turnstile_sitekey", "Turnstile Sitekey"),
            OptionField("turnstile_secret", "Turnstile Secret", widget="password"),
            OptionField("recaptcha_sitekey", "reCAPTCHA Sitekey"),
            OptionField("recaptcha_secret", "reCAPTCHA Secret", widget="password"),
        ],
    ),
    OptionGroup(
        slug="mail",
        title="邮件（SMTP）",
        super_admin_only=True,
        section="services",
        fields=[
            OptionField("mail_host", "SMTP 服务器", help_text="填写后真实发送，留空打印到控制台"),
            OptionField("mail_port", "端口", widget="int"),
            OptionField(
                "mail_encryption", "加密方式", widget="select",
                choices=[("ssl", "SSL（465）"), ("tls", "TLS/STARTTLS（587）")],
            ),
            OptionField("mail_username", "用户名"),
            OptionField("mail_password", "密码/授权码", widget="password"),
            OptionField("mail_from_address", "发件人地址"),
        ],
    ),
    OptionGroup(
        slug="yggdrasil",
        title="Yggdrasil",
        super_admin_only=True,
        section="yggdrasil",
        fields=[
            OptionField(
                "ygg_uuid_algorithm", "UUID 算法", widget="select",
                choices=[("v4", "随机 v4"), ("v3", "离线 v3（盗版一致）")],
            ),
            OptionField("ygg_token_expire_1", "令牌有效期（秒）", widget="int"),
            OptionField("ygg_token_expire_2", "刷新窗口（秒）", widget="int"),
            OptionField("ygg_tokens_limit", "每人令牌上限", widget="int"),
            OptionField("ygg_search_profile_max", "批量查询上限", widget="int"),
            OptionField("ygg_rate_limit", "节流间隔（毫秒）", widget="int",
                        help_text="同一用户/IP 两次请求的最小间隔，1000=1 秒"),
            OptionField("ygg_enable_ali", "API 地址指示（ALI）", widget="bool",
                        help_text="用户输入域名即可自动发现 API 地址，建议开启"),
            OptionField(
                "ygg_skin_domain", "皮肤域名（逗号分隔，留空用当前域名）",
                help_text="客户端白名单规则：.example.com 匹配其子域（不含裸域）；"
                          "example.com 仅精确匹配；* 表示全部放行。当前域名与站点地址会自动加入，"
                          "仅在此额外填写 CDN 等其他域名",
            ),
            OptionField(
                "ygg_private_key", "签名私钥（PEM）", widget="secret_textarea",
                rows=10, monospace=True,
                help_text="保存前会校验 PEM 格式；为安全不回显已存私钥，"
                          "留空保存不修改，粘贴新值即更换。从旧站迁移时粘贴原私钥即可",
            ),
        ],
    ),
    OptionGroup(
        slug="teximport",
        title="皮肤导入",
        super_admin_only=True,
        section="import",
        fields=[
            OptionField("import_enabled", "启用站外导入", widget="bool",
                        help_text="开启后用户可在上传页从其他 Blessing Skin 站导入纹理"),
            OptionField("import_require_review", "导入需管理员审核", widget="bool",
                        help_text="开启后每次导入须在「面板 → 皮肤导入」页批准后才会拉取"),
            OptionField("import_interval_seconds", "拉取间隔（秒）", widget="int",
                        help_text="队列中每两个纹理之间的最小拉取间隔，礼貌爬取建议 ≥ 30"),
            OptionField("import_score_multiplier", "积分倍率", widget="int",
                        help_text="导入消耗 =（体积费 + 收藏费）×倍率，应高于自行上传，无上传奖励"),
            OptionField(
                "import_user_agents", "User-Agent 池（一行一个）", widget="textarea",
                rows=4, monospace=True,
                help_text="拉取时随机选取一行；留空使用内置 UA",
            ),
        ],
    ),
]
