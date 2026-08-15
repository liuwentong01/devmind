# syntax=docker/dockerfile:1.7

# 构建示例：docker build -t devmind/frontend:local frontend
# 运行示例：docker run --rm -p 8080:8080 -e DEVMIND_API_KEY='<普通服务Key>' devmind/frontend:local
# 实际生产应从 Secret 注入 Key，并把容器放在 OIDC/TLS 入口之后。
# 前端只支持同源 API 或用户级会话。共享 API key 由运行时 nginx 从
# Secret 注入上游请求，绝不能作为 VITE_* build arg 编译进 JS。
FROM node:22.14.0-alpine3.21 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27.5-alpine AS runtime

ENV DEVMIND_API_KEY="" \
    NGINX_ENVSUBST_FILTER="^DEVMIND_API_KEY$"

COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html

USER 101:101
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]

CMD ["nginx", "-g", "daemon off;"]
