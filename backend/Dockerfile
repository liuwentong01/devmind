# DevMind 后端镜像（API + worker 共用）
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖（利用层缓存）
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app

EXPOSE 8000

# 默认启动 API；worker 通过覆盖 command 启动（见 K8s worker deployment）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
