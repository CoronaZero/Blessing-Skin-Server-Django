# Blessing Skin Server (Django Edition)

[![CI](https://github.com/CoronaZero/blessing-skin-server-django/actions/workflows/ci.yml/badge.svg)](../../actions)
[![Docker](https://github.com/CoronaZero/blessing-skin-server-django/actions/workflows/docker.yml/badge.svg)](../../actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Blessing Skin Server 的 **Django 重写版**：一个 Minecraft 皮肤站，让玩家上传/分享皮肤与披风，通过内嵌的 Yggdrasil API（authlib-injector 生态）为启动器与服务器提供登录认证与材质分发，内置 3D 皮肤实时预览。

原版（PHP/Laravel）见 <https://github.com/bs-community/blessing-skin-server> ，官方插件仓库见 <https://github.com/bs-community/blessing-skin-plugins> 。本项目逐功能对照原版行为重写，并完成安全加固与结构重设计。

## 功能总览

| 领域 | 能力 |
|---|---|
| 认证 | 邮箱/角色名登录、注册、邮箱验证、一次性密码重置、Cloudflare Turnstile / reCAPTCHA 验证码（默认关闭） |
| 密码兼容 | PHP 原版全部 10 种哈希算法可校验（MD5/SHA 系裸散列、`$2y$` bcrypt、Argon2），登录自动升级 Argon2 |
| 皮肤库 | 上传（PNG 校验/尺寸/比例/重编码消毒）、搜索分类排序分页、hash 去重、积分计费 |
| 渲染 | 2D 头像五种尺寸实时渲染；纹理详情页 **3D 实时预览**（skinview3d，本地托管、零 CDN） |
| Yggdrasil | authenticate / refresh / validate / invalidate / signout / join / hasJoined / Profile 查询 / 材质上传（RSA 签名），authlib-injector 全规范 |
| 用户中心 | 角色管理（四档命名规则）、衣柜、通知、签到、举报、资料修改 |
| OAuth2 | 密码模式 + 授权码 + 刷新令牌（对照原版 Laravel Passport 端点与 scope） |
| 管理后台 | Django Admin 全量定制：用户封禁、纹理管理、举报处理、邀请码、Ygg 令牌吊销 |
| 内嵌能力 | 邀请码注册、批量导入（CLI）、关闭注册、角色数限制、邮箱域名限制（原官方插件能力内置化） |
| 运维 | 安装向导、`migrate_from_php` 数据迁移、健康探针、ghcr 镜像 |

## 与原版（PHP）的主要差异

- **插件系统 → 能力内嵌**：不再支持运行时 PHP 插件加载，高频插件能力改为内置功能（管理后台开关控制）
- **数据库 Schema 全新设计**，提供迁移命令从 PHP 版无损搬迁（见下文「数据迁移」）
- **管理后台使用 Django Admin**，替代原版自研后台
- **安全修复**（源自对原版的静态安全审查）：
  - OAuth 登录绑定第三方用户 ID（防账号接管）
  - 密码重置链接一次性 + 哈希落库
  - Yggdrasil 限流按 IP（原版可被伪造用户名撑爆缓存）、请求体大小上限
  - 账号枚举防护开关（默认开启）
- **前端 Tailwind 设计系统重写**，14 个自绘 SVG 图标，全部静态资源本地提供（不使用第三方 CDN）
- **3D 预览前端化**（原版为后端 Imagick 静态投影），观感与性能大幅提升

## 环境要求

- Python 3.12+
- 数据库：SQLite（默认）/ MySQL 8+ / PostgreSQL 14+
- 生产：Docker（推荐）或任意 WSGI/ASGI 托管环境

## 快速开始（本地开发）

```bash
# 1. 虚拟环境（约定：venv 在项目根目录内）
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Linux/macOS

# 2. 依赖
pip install -r requirements-dev.txt

# 3. 数据库（SQLite）
python manage.py migrate
python manage.py createsuperuser   # 或访问 /setup/ 使用安装向导

# 4. 启动
python manage.py runserver
```

访问 <http://127.0.0.1:8000/>，后台在 `/admin/`，未安装时 `/setup/` 提供向导。

## 配置

基础设施配置走 **环境变量**（`.env` 自动加载，样例见 [`.env.example`](.env.example)）；站点业务配置（站点名、SMTP、验证码、积分规则等）在 **管理后台 → Options** 中修改（Option 表存储，运行时生效）。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `SECRET_KEY` | 开发生成 | 签名密钥。**生产必须设置**：`python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `DEBUG` | `true` | 生产必须 `false`；且缺 `SECRET_KEY` 时拒绝启动 |
| `ALLOWED_HOSTS` | 空 | 逗号分隔域名 |
| `DB_CONNECTION` | `sqlite` | `sqlite` / `mysql` / `pgsql` |
| `DB_HOST` / `DB_PORT` | 127.0.0.1 + 端口 | 数据库地址 |
| `DB_DATABASE` / `DB_USERNAME` / `DB_PASSWORD` | — | 库名与凭据 |
| `SALT` | 空 | 迁移场景：原站 `SALTED2*` 算法历史用户校验所需 |

Settings 分层：`config/settings/{base,dev,test,prod}.py`；`manage.py` 默认 dev，容器入口默认 prod，`DJANGO_SETTINGS_MODULE` 可覆盖。

**HTTPS 反向代理部署（Nginx / 1Panel 等）要点**：

- `CSRF_TRUSTED_ORIGINS` 从 `ALLOWED_HOSTS` 自动派生（含 https/http 两种 scheme）——Django 4+ 对 POST 校验 Origin，不配则登录等提交一律 403
- 应用自动识别反代传递的 `X-Forwarded-Proto` 头；代理侧确保转发该头（1Panel/OpenResty 反代配置通常默认带）
- 全站 HTTPS 后建议环境变量 `COOKIE_SECURE=1`（Session 与 CSRF Cookie 加 Secure 标记）
- 管理后台 Options 里设置 `site_url=https://你的域名`——邮件链接、安装向导收集的站点地址用它构建

MySQL 驱动已内置：**PyMySQL**（纯 Python，经 `install_as_MySQLdb()` shim 满足 Django 后端的 MySQLdb 接口，零系统依赖）。改用 `mysqlclient`（性能更佳但需系统 `libmysqlclient`）时：`pip install mysqlclient` 并移除 `config/__init__.py` 中的 shim。PgSQL 驱动（`psycopg[binary]`）按需安装。

## 数据迁移（从 PHP 版）

**方式一（推荐）：指定 PHP 站点 `.env`，一键完整迁移（数据 + 皮肤文件）**

```bash
# 预检（不写入任何数据、不复制任何文件，查看行数与文件完整性报告）
python manage.py migrate_from_php --env /path/to/php-site/.env --dry-run

# 正式迁移
python manage.py migrate_from_php --env /path/to/php-site/.env
```

自动从 `.env` 读取：数据库连接（`DB_*`）、密码盐（`SALT`，`SALTED2*` 历史用户登录依赖）、皮肤目录（`TEXTURES_DIR` 或默认 `<站点>/storage/textures`）。数据库逐表搬运后，**将用户上传的皮肤文件全部物理复制到新站存储目录**（如 `./data/textures/`，不引用旧站路径），逐文件 sha256 校验，缺失/损坏文件输出清单（数据库记录保留，角色引用不断链）。重跑幂等。

**`.env` 各键的迁移处理**：

| 键 | 处理 |
|---|---|
| `DB_CONNECTION` / `DB_HOST` / `DB_PORT` / `DB_DATABASE` / `DB_USERNAME` / `DB_PASSWORD` / `DB_PREFIX` | 用于连接旧库（`DB_PREFIX` 由 `--table-prefix` 对应） |
| `SALT` | 注入运行环境，`SALTED2*` 算法历史用户登录立即可用；`PWD_METHOD` 为 `SALTED2*` 而缺盐时迁移命令会报错提示 |
| `PWD_METHOD` | 仅展示于报告（Django 版按散列特征自动识别全部 10 种算法，不依赖此键） |
| `TEXTURES_DIR` | 皮肤文件复制源目录 |
| `APP_KEY` | **忽略**——session 不跨站迁移；密码重置链接在本站为一次性数据库令牌，无需 Laravel 签名密钥 |
| `JWT_SECRET` | **忽略**——旧站 Yggdrasil 令牌存于其缓存且 3-7 天有效期，无可搬数据；玩家重新登录即签发新令牌 |
| `APP_NAME` / 其他站点键 | 站点名展示于报告；站点配置随 options 表搬运 |

> Docker 部署时：把 PHP 站点目录挂进容器执行，如
> `docker compose exec blessing-skin python manage.py migrate_from_php --env /mnt/php-site/.env`
> （compose 中给 blessing-skin 服务临时加一行 `- /path/to/php-site:/mnt/php-site:ro`）

**方式二：仅迁移数据库（手拼 DSN）**

```bash
python manage.py migrate_from_php --source "mysql://user:pass@host/dbname"
```

两种方式均：用户密码散列原样保留（登录时自动校验并升级 Argon2）、角色 + UUID 映射并入、衣柜/举报/选项搬运、事务化可重跑、行数比对报告。已签发 token 不迁移（用户重新授权一次）。

## 部署

### Docker Compose（推荐）

仓库根提供了开箱即用的 [`docker-compose.yml`](docker-compose.yml)（应用 + MySQL 8，含健康检查与数据持久化）：

```bash
# 1. 编辑 docker-compose.yml，修改三处「必改」：
#    SECRET_KEY（生成命令见下）、数据库密码（两处须一致）
vi docker-compose.yml

# 2. 启动（首次自动拉镜像）
docker compose up -d

# 3. 查看状态（healthy 为就绪）
docker compose ps
docker compose logs -f blessing-skin
```

首次访问 `http://<host>:8000/setup/` 完成安装向导。

**升级**：`docker compose pull && docker compose up -d`（镜像启动时自动迁移数据库）。

**备份**：纹理文件在 `./data/textures/`；MySQL 数据在命名卷 `mysql-data`（`docker run --rm -v blessing-skin-server-django_mysql-data:/data -v $PWD:/backup alpine tar czf /backup/mysql-data.tgz /data`）。

**SQLite 极简起步**：不想跑 MySQL 时，按 [`docker-compose.yml`](docker-compose.yml) 尾部注释裁剪——删除 `db` 服务、`DB_CONNECTION` 改 `sqlite`、挂载 `./data` 目录。

### Docker 单容器

```bash
docker run -d --name blessing-skin \
  -p 8000:8000 \
  -e SECRET_KEY=<密钥> \
  -e ALLOWED_HOSTS=skin.example.com \
  -e DB_CONNECTION=mysql -e DB_HOST=<host> -e DB_USERNAME=<u> -e DB_PASSWORD=<p> -e DB_DATABASE=<db> \
  -v /srv/skins:/app/storage/textures \
  ghcr.io/coronazero/blessing-skin-server-django:latest
```

镜像特性：多阶段构建、非 root 运行、whitenoise 静态托管（无需 Nginx）、启动时自动 `migrate`、gunicorn + uvicorn worker、`/healthz` 探针。

首次访问 `http://<host>:8000/setup/` 完成安装向导。

### 直接部署（无 Docker）

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic
gunicorn config.wsgi -b 0.0.0.0:8000 -w 4
# 或 ASGI：uvicorn config.asgi:application --host 0.0.0.0 --port 8000
```

### 批量导入存量纹理

```bash
python manage.py import_textures /path/to/pngs --public            # <名>.png / <名>.alex.png / <名>.cape.png
python manage.py import_textures /path/to/pngs --dry-run           # 预检
```

## 开发约定

- **TDD**：先测试后实现（`pytest`，SQLite 内存库），312+ 项契约/回归测试
- **原子提交**：`feat/fix/chore/docs/test/refactor: 中文描述`，GPG 签名
- **代码风格**：`ruff check .` 零告警；命名英文、注释中文
- **行为对照**：与 PHP 原版同输入同输出为验收标准（hasher 已知向量、Yggdrasil 协议用例、上传校验链测试锚定原版行为）

## 项目结构

```
├── config/          # settings 分层、根路由、migrate_from_php 命令
├── accounts/        # 用户、密码兼容层（10 种 PHP 算法）、认证后端、验证码
├── players/         # 角色 + UUID 生成（v3 离线算法/v4）
├── textures/        # 校验消毒、存储、2D/3D 渲染、皮肤库 API、批量导入
├── yggdrasil/       # authlib-injector 认证服务（JWT、会话、RSA 签名）
├── moderation/      # 举报
├── notifications/   # 站内通知
├── invitation/      # 邀请码（内嵌能力）
├── setup/           # 安装向导
├── templates/       # Django 模板（Tailwind 设计系统）
└── static/          # 自绘图标、设计系统 CSS、交互 JS、skinview3d vendor
```

## License

MIT（沿用原版授权；`static/vendor/skinview3d.LICENSE` 为内嵌库的 MIT 许可）
