# DevMind K8s 部署

> 说明：本目录是生产部署骨架。PostgreSQL/Redis/Neo4j 假定已有（用云托管或单独 StatefulSet）。
> ConfigMap 里的 `*_URL` 按实际地址改。

## 构建并推镜像

```bash
docker build -t devmind/backend:latest backend
docker build -t devmind/frontend:latest frontend
# docker push ...（推到你的镜像仓库，并把 image 改成对应地址）
```

## 部署

```bash
kubectl apply -f deploy/k8s/namespace.yaml
cp deploy/k8s/secret.example.yaml deploy/k8s/secret.yaml   # 填入真实密钥
kubectl apply -f deploy/k8s/secret.yaml
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/backend.yaml
kubectl apply -f deploy/k8s/worker.yaml
kubectl apply -f deploy/k8s/frontend.yaml
kubectl apply -f deploy/k8s/ingress.yaml
```

## 组件

| 资源 | 说明 |
|---|---|
| `devmind-backend` | FastAPI API，2 副本，带 readiness/liveness 探针 |
| `devmind-worker` | ARQ 异步 worker（同镜像，命令覆盖为 `arq ...`） |
| `devmind-frontend` | nginx 托管前端 + 反代 `/api` 到后端 |
| `devmind-ingress` | 入口；已关闭 proxy 缓冲以支持 SSE 流式 |

> secret.yaml 含真实密钥，已被 .gitignore 忽略，请勿提交。
