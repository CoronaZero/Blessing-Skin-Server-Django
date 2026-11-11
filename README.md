# Blessing Skin Server (Django Edition)

[Docker 构建 GHCR] · [CI 测试] · MIT License

Blessing Skin Server 的 **Django 重写版**：一个 Minecraft 皮肤站，让玩家上传/分享皮肤与披风，并通过内嵌的 Yggdrasil API（authlib-injector 生态）为启动器与服务器提供登录认证与材质分发。

原版（PHP/Laravel）见同目录 `blessing-skin-server-php`，官方插件仓库见 `blessing-skin-plugins`。本项目按逐功能对照原版行为重写，并做了安全加固与结构重设计（详见下文）。

## 与原版（PHP）的主要差异

- **插件系统 → 能力内嵌**：不再支持运行时 PHP 插件加载。原官方插件中的高频能力（OAuth 登录、云存储、邀请码等约 15 个）以内置功能形式重写，通过管理后台开关启用。
- **数据库 Schema 全新设计**，但提供 `migrate_from_php` 管理命令，从 PHP 版数据库无损搬迁数据（详见「数据迁移」）。
- **管理后台使用 Django Admin**，替代原版自研后台。
- **安全修复**（来自对原版的静态安全审查）：
  - OAuth 登录绑定第三方用户 ID，不再仅凭 email 匹配（防账号接管）
  - 密码重置链接一次性使用 + 哈希存储
  - Yggdrasil API 限速按来源 IP，请求体大小上限
- **验证码支持 Cloudflare Turnstile**（默认关闭，后台可开启），同时保留 reCAPTCHA 兼容。
- **部署形态**：单容器（自带 uvicorn/gunicorn）或直接部署，替代原版 PHP-FPM + Composer。

## 环境要求

- Python 3.12+
- 数据库：SQLite（默认，开箱即用）/ MySQL 8+ / PostgreSQL 14+（Python 驱动按需安装，见下）

## 快速开始（本地开发）

```bash
# 1. 创建虚拟环境（约定：venv 在项目根目录内）
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate

# 2. 安装依赖
pip install -r requirements-dev.txt

# 3. 初始化数据库（SQLite）
python manage.py migrate
python manage.py createsuperuser

# 4. 启动开发服务器
python manage.py runserver
```

访问 <http://127.0.0.1:8000/healthz> 确认服务已启动，<http://127.0.0.1:8000/admin/> 进入后台。

## 配置

配置支持 **环境变量** 与 **Django 配置文件** 两种方式（环境变量优先），命名沿用 PHP 原版 `.env` 以便迁移时直接复用。完整样例见 [`.env.example`](.env.example)。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `SECRET_KEY` | 开发模式自动生成 | 签名密钥。**生产必须显式设置**，生成方式：`python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `DEBUG` | `true` | 生产必须 `false`。`false` 且缺 `SECRET_KEY` 时拒绝启动 |
| `ALLOWED_HOSTS` | 空 | 逗号分隔的站点域名 |
| `DB_CONNECTION` | `sqlite` | `sqlite` / `mysql` / `pgsql` |
| `DB_HOST` / `DB_PORT` | `127.0.0.1` + 3306/5432 | 数据库地址 |
| `DB_DATABASE` / `DB_USERNAME` / `DB_PASSWORD` | — | 名称与凭据；SQLite 时为文件路径 |
| `ENV_FILE` | `./.env` | .env 文件位置 |

MySQL/PgSQL 需要 `mysqlclient` / `psycopg` 驱动（为保持默认安装轻量，未列入 requirements，生产按需安装）。

Settings 模块按环境分层：`config/settings/{base,dev,test,prod}.py`，`manage.py` 默认 dev，`wsgi.py`/`asgi.py` 默认 prod，可用 `DJANGO_SETTINGS_MODULE` 覆盖。

## 数据迁移（从 PHP 版）

```bash
python manage.py migrate_from_php --source "mysql://user:pass@host/dbname"
```

迁移命令只读接入旧库，按映射表写入新库：用户（含 `password` 原样搬运，登录时自动升级哈希算法）、玩家与 UUID 映射、纹理（hash 不变，皮肤文件零搬动）、衣柜、选项设置、OAuth 客户端等，并输出逐表行数比对报告。（命令在后续 Phase 落地，此处为契约说明）

## 部署

### Docker（生产）

```bash
docker run -d --name blessing-skin \
  -p 8000:8000 \
  -e SECRET_KEY=<你的密钥> \
  -e ALLOWED_HOSTS=skin.example.com \
  -e DB_CONNECTION=mysql \
  -e DB_HOST=<db-host> \
  ... \
  ghcr.io/<owner>/blessing-skin-server:<tag>
```

镜像自带 gunicorn + uvicorn worker，入口直接可用；数据卷：数据库未容器化时挂载 SQLite 文件目录与皮肤文件目录。

### 直接部署（无 Docker）

```bash
pip install -r requirements.txt gunicorn
python manage.py migrate
python manage.py collectstatic
gunicorn config.wsgi -b 0.0.0.0:8000 -w 4
```

## 开发约定

- **TDD**：先写测试再写实现，`pytest` 运行（SQLite 内存库）。
- **原子提交**：前缀英文（`feat`/`fix`/`chore`/`docs`/`test`/`refactor`）+ 中文描述。
- **代码风格**：`ruff check .` 零告警；行宽 100；命名英文、注释中文。
- **测试驱动行为对照**：与 PHP 原版同输入同输出作为验收标准（hasher 向量表、纹理解码黄金对比、Yggdrasil 协议用例等）。

## 路线图

- [x] Phase 0：骨架（settings TDD、CI、DOT password grant 验证）
- [ ] Phase 1：数据层（全新 schema + `migrate_from_php`）
- [ ] Phase 2：认证基座（10 种密码哈希兼容、注册/登录、Turnstile）
- [ ] Phase 3：纹理服务（静态 API、2D 渲染、上传消毒）
- [ ] Phase 4：Yggdrasil API（认证/会话/签名）
- [ ] Phase 5：Web 前台
- [ ] Phase 6：管理后台（Django Admin 定制）
- [ ] Phase 7：OAuth2 + 安装向导
- [ ] Phase 8：3D 渲染收尾、基准测试、镜像发布

## License

MIT（沿用原版授权）
