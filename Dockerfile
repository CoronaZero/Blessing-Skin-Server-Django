# syntax=docker/dockerfile:1

# ---- 构建阶段 ----
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# ---- 运行阶段 ----
FROM python:3.12-slim

# 生产依赖：gunicorn(WSGI) + uvicorn(ASGI) 均已含于 requirements
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# 非 root 运行
RUN useradd --create-home --uid 1000 blessing
WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=blessing:blessing . .

# 静态文件收集（whitenoise 托管）。
# 构建期仅需一次性占位密钥让 settings 加载（collectstatic 不签名任何内容，
# 不落盘不进运行时）；运行时 SECRET_KEY 由部署方经环境变量注入。
RUN SECRET_KEY=build-time-placeholder-for-collectstatic \
    python manage.py collectstatic --no-input && \
    chown -R blessing:blessing /app

USER blessing

EXPOSE 8000

# 数据库迁移与入口启动（gunicorn + uvicorn worker，生产就绪）
CMD ["sh", "-c", "python manage.py migrate --no-input && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --worker-class uvicorn.workers.UvicornWorker --access-logfile -"]
