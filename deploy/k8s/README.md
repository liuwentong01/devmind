# DevMind Kubernetes 生产部署基线

本目录是安全默认的参考基线，不是开箱即用的生产环境。上线前必须完成企业 OIDC/RBAC、托管数据服务、密钥托管、备份恢复、容量/SLO 和告警验收；总清单见 [`../../docs/生产上线检查清单.md`](../../docs/生产上线检查清单.md)。

## 前置条件

- Kubernetes 1.27+、Metrics Server、支持 `networking.k8s.io/v1` 的 ingress-nginx。
- 已部署 OAuth2 Proxy（或等价 external auth）。`ingress.yaml` 中的默认 auth 地址是 fail-closed 占位，未替换时 UI 不会匿名放行。
- PostgreSQL/pgvector、Redis、Neo4j 使用托管高可用服务；这里不在集群内创建有状态数据库。
- 上游强制 TLS：PostgreSQL URL 含 `sslmode=require|verify-ca|verify-full`，Redis 使用 `rediss://`，Neo4j 使用 `neo4j+s://` 或 `bolt+s://`，启用的外部 HTTP 集成使用 `https://`。
- cert-manager 或企业证书系统已创建 `devmind-tls` 与 `devmind-webhooks-tls`。
- External Secrets/Sealed Secrets/企业密钥系统负责创建运行时 `devmind-secret` 和仅迁移 Job 可见的 `devmind-migration-secret`。两个 `*.example.yaml` 只描述键结构，禁止直接应用。

## 发布制品

镜像应由 CI 构建、扫描、生成 SBOM 并签名；清单中禁止使用 `latest`。发布前将 backend、worker、migration 与 frontend 的 `image` 全部替换为同一发布版本的不可变 digest，例如：

```text
ghcr.io/example/devmind-backend@sha256:<digest>
ghcr.io/example/devmind-frontend@sha256:<digest>
```

本地验证构建：

```bash
docker build -t devmind/backend:verify backend
docker build -t devmind/frontend:verify frontend
```

## 必改配置

1. 修改 `configmap.yaml` 的 UI/Webhook 域名、CORS、Neo4j 地址与 `CODE_INDEX_ALLOWED_ROOTS`。
2. 将 `FORWARDED_ALLOW_IPS` 改为集群实际可信代理/Pod CIDR；禁止 `*`。
3. 用只读 PVC/CSI 代码卷替换 `backend.yaml` 中 `/workspace` 的占位 `emptyDir`。
4. 修改 `ingress.yaml` 的 OAuth2 Proxy 地址、登录地址、TLS secret 和 ingress controller namespace。
5. `DEVMIND_FRONTEND_API_KEY` 是 nginx 到 API 的普通服务凭据，必须包含在 `DEVMIND_API_KEYS` 中，且不得与管理 key 复用。它不是用户身份；用户认证与组/租户授权必须由 OIDC/API Gateway 完成。
6. `/api/admin/*` 仅接受管理 key。生产前端不展示成本管理页；运维只能通过受审计的独立管理通道调用，不能把普通前端服务 key 提升为 admin。
7. `GITHUB_WEBHOOK_SECRET`、`ALERT_WEBHOOK_SECRET`、普通 key 与管理 key 均至少 32 个随机字符并制定轮换流程。
8. Secret 只配置 `LLM_DEFAULT_MODEL` / `EMBEDDING_MODEL` 实际使用的供应商 Key；应用会按模型前缀校验并在缺失时拒绝生产启动。`ALLOW_UNVALIDATED_MODEL_PROVIDER` 默认必须为 `false`，仅经安全评审且由工作负载身份提供认证的自定义供应商可例外。

## 数据库迁移与发布顺序

先备份数据库。若数据库来自旧版本 `create_all` 且已有业务表但没有 Alembic 版本，必须在预生产核对结构后执行一次：

```bash
alembic stamp 20260815_0000
alembic upgrade head
```

新环境或已纳管环境按以下顺序发布。任何一步失败都停止，不要先启动新 API：

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/serviceaccount.yaml
kubectl apply -f deploy/k8s/configmap.yaml

# 由企业密钥控制器创建；迁移账号有 DDL 权限，运行时账号只保留 DML
kubectl -n devmind get secret devmind-secret devmind-migration-secret

kubectl apply -f deploy/k8s/migration.yaml
kubectl -n devmind wait \
  --for=condition=complete job/devmind-db-migrate-20260815-0002 \
  --timeout=10m

kubectl apply -f deploy/k8s/backend.yaml
kubectl apply -f deploy/k8s/worker.yaml
kubectl apply -f deploy/k8s/frontend.yaml
kubectl apply -f deploy/k8s/pdb.yaml
kubectl apply -f deploy/k8s/autoscaling.yaml
kubectl apply -f deploy/k8s/networkpolicy.yaml

# 仅在 OIDC、域名、TLS 和 webhook 路由均已验证后创建公网入口
kubectl apply -f deploy/k8s/ingress.yaml
```

每次新增 Alembic revision 时同步修改 `migration.yaml` 的 Job 名称；已完成的固定名称 Job 不会因 `kubectl apply` 自动重跑。

## 入口与鉴权边界

- `devmind.example.com`：UI 与普通 API，全部先经 OAuth2 Proxy/OIDC，再到前端 nginx；nginx 在服务端注入普通 API key，密钥不会进入 JS bundle。
- `devmind-webhooks.example.com`：只开放 `Exact` 的 GitHub 与 Alertmanager 两个路径，直达 backend，不走交互式 OIDC；应用分别强制校验 GitHub HMAC 与 Alert Bearer secret。
- 两个 Ingress 使用不同 host，避免主站 `/` Prefix 抢占 webhook 规则。上线前仍需在真实 ingress-nginx 版本上验证生成的 location，并在 WAF/API Gateway 加来源约束。
- Ingress 的连接数/RPS 是基础保护。SSE 会长期占用连接，必须按企业 NAT、controller 副本和压测数据调参；用户/租户配额需要在 OIDC/API Gateway 层实现。

## 上线验证

```bash
kubectl -n devmind rollout status deployment/devmind-backend --timeout=10m
kubectl -n devmind rollout status deployment/devmind-worker --timeout=10m
kubectl -n devmind rollout status deployment/devmind-frontend --timeout=10m
kubectl -n devmind get pods,pdb,hpa,ingress
kubectl -n devmind describe job devmind-db-migrate-20260815-0002
```

还应从集群外验证：匿名访问 UI 被 OIDC 拦截；普通用户不能调用 `/api/admin/*`；错误/缺失 Webhook 签名被拒绝；SSE 经两层代理仍逐 token 到达；数据库/Redis/Neo4j 故障会让 readiness 或业务请求明确失败且不会返回伪造结果。

HPA 依赖 Metrics Server。worker 默认 2 副本并带 PDB；生产建议进一步使用 KEDA/队列深度指标扩缩容，CPU HPA 只是通用保底。

## 回滚边界

应用使用 `RollingUpdate(maxUnavailable=0)`，可在 schema 向后兼容时执行 `kubectl rollout undo`。数据库迁移默认采用向前修复：不要在未验证数据可逆性时自动 downgrade。发布前必须确认“旧代码 + 新 schema”可共存，破坏性变更使用 expand/migrate/contract 多阶段发布，并保留已验证的 PITR 恢复点。
