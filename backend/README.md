# DevMind 后端（P0 内核）

FastAPI + LiteLLM(国内模型) + LangGraph 最小 Agent 循环，提供 SSE 流式对话接口。

> 本页 curl 默认用于 `APP_ENV=local`（本地默认关闭 API key 鉴权）。非本地环境必须给业务/API 请求增加 `X-API-Key`，管理端点必须使用独立管理 key；不要把共享 key 写进浏览器 bundle。Webhook 不使用该 header：GitHub 必须带标准 `X-Hub-Signature-256` HMAC，告警入口必须带 `Authorization: Bearer <ALERT_WEBHOOK_SECRET>`。

## 目录结构

```
app/
├── main.py                     # FastAPI 入口
├── core/                       # 配置 / 日志 / 可观测
│   ├── config.py
│   ├── logging.py
│   └── observability.py        # Langfuse 回调（可选）
├── kernel/                     # ★ Agent 内核（共享）
│   ├── llm/gateway.py          # LiteLLM 网关 → DeepSeek / 通义 / 智谱
│   ├── tools/builtin.py        # 示例工具（时间 / 计算）
│   └── orchestrator/graph.py   # LangGraph 最小 ReAct Agent
├── gateway/routes.py           # /api/health、/api/chat/stream
└── schemas/chat.py             # 请求 / 响应模型
```

## 本地运行

### 1. 准备环境

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .          # 或 uv pip install -e .
cp .env.example .env
```

### 数据库迁移

首次建库和每次发布都应在启动 API/worker 前执行：

```bash
alembic upgrade head
```

生产建议通过 `MIGRATION_DATABASE_URL` 给 Alembic 提供独立 DDL 角色；API/worker 的
`DATABASE_URL` 使用只具备运行时 DML 权限的账号。托管 PostgreSQL 还应由 DBA 预先
启用 pgvector，避免长期授予应用账号 `CREATE EXTENSION` 权限。

旧版本若已由 `Base.metadata.create_all` 建出 `documents` / `chunks`，先备份并确认表结构
仍为旧版基线，再执行：

```bash
alembic stamp 20260815_0000
alembic upgrade head
```

`0001` 会增加 `(document_id, chunk_index)` 唯一约束；旧库升级前先执行以下只读检查，
若返回任何行，必须按业务保留策略清理重复数据后再迁移（迁移会 fail closed）：

```sql
SELECT document_id, chunk_index, COUNT(*)
FROM chunks
GROUP BY document_id, chunk_index
HAVING COUNT(*) > 1;
```

非本地环境会校验 Alembic revision 与数据库中 `vector(N)` 的实际维度，不匹配时拒绝
启动。初始迁移使用 `vector(1024)`；更换 `EMBEDDING_DIM` 必须先编写并执行显式迁移，
不能只改环境变量。

需求拆解的待确认任务、Jira 写入进度与结果默认保留 24 小时。ARQ worker
每小时清理过期数据；处理中计划会保留到其租约也过期，避免在 Jira 批量写入期间丢失幂等进度。

### 2. 配置模型 Key

编辑 `.env`，填入你要用的国内模型 API Key（至少一个）：

```dotenv
# 默认用 DeepSeek
LLM_DEFAULT_MODEL=deepseek/deepseek-chat
DEEPSEEK_API_KEY=sk-xxxxx

# 或切到通义 / 智谱：
# LLM_DEFAULT_MODEL=dashscope/qwen-max
# DASHSCOPE_API_KEY=xxxxx
# LLM_DEFAULT_MODEL=zhipuai/glm-4
# ZHIPUAI_API_KEY=xxxxx
```

### 3. 启动

```bash
uvicorn app.main:app --reload --port 8000
```

- 存活检查：<http://localhost:8000/api/health/live>
- 就绪检查：<http://localhost:8000/api/health/ready>
- 接口文档：<http://localhost:8000/docs>

### 4. 测试流式对话

```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"现在几点了？再算一下 12*(3+4)"}]}'
```

应能看到 Agent 调用 `get_current_time` / `calculate` 工具并流式返回结果。

## P1 知识答疑（RAG）

前置：起 `docker compose -f ../deploy/docker-compose.yml up -d`（PG+pgvector），
并在 `.env` 配好 `EMBEDDING_MODEL` + 对应厂商 Key（如 `DASHSCOPE_API_KEY`）。

```bash
# 1. 入库一篇文档
curl -X POST http://localhost:8000/api/knowledge/documents \
  -H "Content-Type: application/json" \
  -d '{"title":"请假制度","content":"员工请假需提前一天在 OA 提交申请，病假需附医院证明。年假每年 10 天。"}'

# 2. 查看文档列表
curl http://localhost:8000/api/knowledge/documents

# 3. RAG 流式问答（先返回 sources，再流式 token）
curl -N -X POST http://localhost:8000/api/knowledge/query/stream \
  -H "Content-Type: application/json" \
  -d '{"question":"病假需要什么材料？"}'
```

### 知识答疑接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/knowledge/documents` | 文档入库（分块 + 向量化） |
| GET | `/api/knowledge/documents` | 文档列表（含切片数） |
| DELETE | `/api/knowledge/documents/{id}` | 删除文档（级联切片） |
| POST | `/api/knowledge/query/stream` | RAG 流式问答（SSE：sources + token） |

## P2 代码审查（事件驱动 + 异步队列）

团队规范检索复用 P1 的 PostgreSQL/pgvector 与 Embedding，因此手动和异步审查都需
这些依赖；Webhook 路径另外需要 Redis，回写 GitHub 还需要最小权限 Token。

```bash
# 手动流式审查（无需 GitHub，直接体验）
curl -N -X POST http://localhost:8000/api/review/stream \
  -H "Content-Type: application/json" \
  -d '{"diff":"diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-x=1\n+x = 1"}'

# 启动异步 worker（Webhook / trigger 路径必需，需先起 Redis）
arq app.workers.review_worker.WorkerSettings

# 触发对某 PR 的异步审查（入队，由 worker 拉 diff 并回写）
curl -X POST http://localhost:8000/api/review/trigger \
  -H "Content-Type: application/json" \
  -d '{"owner":"liuwentong01","repo":"devmind","number":1}'
```

### 代码审查接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/review/stream` | 手动流式审查（SSE，粘 diff） |
| POST | `/api/review/trigger` | 触发对某 PR 的异步审查（入队） |
| POST | `/api/webhooks/github` | GitHub PR Webhook 接收器（验签 → 入队） |

> Webhook / trigger 需要：Redis 运行中 + 启动 `arq` worker + `.env` 配 `GITHUB_TOKEN`（回写 PR）。

## P3 告警根因（AIOps：并行编排 + 假设验证循环）

仅 `local/test` 在未配置 Prometheus/Loki/DEPLOY_REPO 时使用 mock 数据。非本地环境会明确返回证据缺失，绝不会伪造监控结果。

```bash
# 手动流式根因分析（SSE：step 进度 + 报告 token）
curl -N -X POST http://localhost:8000/api/incident/analyze/stream \
  -H "Content-Type: application/json" \
  -d '{"alertname":"HighMemoryUsage","service":"order-service","severity":"critical","description":"内存超 95%，5xx 飙升"}'

# 监控告警 Webhook（Alertmanager 格式）→ 入队 → worker 分析并推 IM
curl -X POST http://localhost:8000/api/webhooks/alert \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ALERT_WEBHOOK_SECRET}" \
  -d '{"alerts":[{"status":"firing","labels":{"alertname":"HighMemoryUsage","service":"order-service","severity":"critical"},"annotations":{"description":"内存超 95%"}}]}'
```

### 告警根因接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/incident/analyze/stream` | 手动流式根因分析（SSE） |
| POST | `/api/webhooks/alert` | 告警 Webhook（Alertmanager 格式）→ 入队 |

## 统一异步 worker

P2 / P3 的异步任务由同一个 worker 处理：

```bash
arq app.workers.worker.WorkerSettings
```

## P5 代码理解（tree-sitter + Neo4j 代码图谱）

前置：起 Neo4j（compose 已含），`.env` 配 `NEO4J_*`。

```bash
# 1. 索引一个本地仓库（构建代码图谱）
curl -X POST http://localhost:8000/api/codebase/index \
  -H "Content-Type: application/json" \
  -d '{"repo":"devmind","path":"/srv/devmind/backend"}'

# 2. 代码问答（SSE：先回相关文件，再流式回答）
curl -N -X POST http://localhost:8000/api/codebase/query/stream \
  -H "Content-Type: application/json" \
  -d '{"repo":"devmind","question":"index_repo 这个函数做了什么？"}'

# 3. 改动影响分析（反向调用遍历）
curl -X POST http://localhost:8000/api/codebase/impact \
  -H "Content-Type: application/json" \
  -d '{"repo":"devmind","function":"retrieve","depth":3}'
```

### 代码理解接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/codebase/index` | 索引本地仓库，构建 Neo4j 代码图谱 |
| POST | `/api/codebase/query/stream` | 代码库问答（SSE） |
| POST | `/api/codebase/impact` | 改动影响分析（调用者 + 被调用者 + 总结） |

> Neo4j Browser: <http://localhost:7474>（账号 neo4j / 密码见 `.env` 的 `NEO4J_PASSWORD`），
> 可视化查看代码图谱：`MATCH (f:Function {repo:"devmind"})-[:CALLS]->(g) RETURN f,g LIMIT 100`。

## P4 需求拆解（HITL 人工闸门）

`local/test` 可显式开启 Jira dry-run 体验 HITL；非本地环境强制关闭 dry-run，未配置 Atlassian 时确认请求会失败：

```bash
# 1. 拆解（计划写入 PostgreSQL，返回 thread_id + 提议任务，但尚未写 Jira）
curl -X POST http://localhost:8000/api/requirement/plan \
  -H "Content-Type: application/json" \
  -d '{"prd_text":"用户可用积分兑换商品；运营后台配置商品；兑换记录可导出","project":"DEV"}'

# 2. 人工确认（用上一步返回的 thread_id + 编辑后的 tasks）→ 写 Jira
curl -X POST http://localhost:8000/api/requirement/confirm \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"<上一步返回>","tasks":[{"title":"商品兑换接口","type":"backend","story_points":5}]}'
```

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/requirement/plan` | 拆解 PRD → 持久化待确认计划 → 返回提议 |
| POST | `/api/requirement/confirm` | 原子认领计划 → 幂等写 Jira → 缓存结果 |

计划和逐任务写入进度保存在 PostgreSQL，因此 `plan` / `confirm` 可落到不同 API 副本；
重复确认会返回第一次的持久化结果，中途失败只续写尚未完成的 Jira 任务。

## P6 工程化

```bash
# 成本治理
curl http://localhost:8000/api/admin/cost

# 评测（需先在知识答疑入库请假制度示例文档）
python -m app.eval.run_eval
```

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/cost` | LLM 用量与费用统计 |
| POST | `/api/admin/cost/reset` | 重置计数 |

部署见 `../deploy/k8s/README.md`。
