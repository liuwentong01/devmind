# syntax=docker/dockerfile:1.7

# 构建：docker build -t devmind/backend:local backend
# API：docker run --rm --env-file backend/.env -p 8000:8000 devmind/backend:local
# Worker：使用同一镜像覆盖命令为 arq app.workers.worker.WorkerSettings。
# API 与 ARQ worker 共用同一镜像。基础镜像固定到 Python 补丁版本，
# 应由镜像发布流水线继续解析并记录 digest/SBOM。
FROM python:3.11.13-slim-bookworm AS dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build
COPY requirements.lock ./
# --require-hashes 保证下载制品与评审后的 lock 一致；--no-deps 防止 pip 隐式解析漂移。
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --require-hashes --no-deps -r requirements.lock

FROM python:3.11.13-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    FORWARDED_ALLOW_IPS=127.0.0.1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN groupadd --gid 10001 devmind \
    && useradd --uid 10001 --gid devmind --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin devmind

WORKDIR /app
COPY --from=dependencies --chown=10001:10001 /opt/venv /opt/venv
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 alembic ./alembic
COPY --chown=10001:10001 alembic.ini ./alembic.ini

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; host=os.getenv('TRUSTED_HOSTS', '127.0.0.1').split(',')[0].strip(); request=urllib.request.Request('http://127.0.0.1:8000/api/health/live', headers={'Host': host}); urllib.request.urlopen(request, timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
