# DevMind · 研发效能智能体平台

AI 原生的内部研发效能平台。5 个垂直 Agent（代码审查 / 告警根因 / 需求拆解 / 知识答疑 / 代码理解）共享同一套「Agent 内核」。

> 完整技术方案见 [`docs/研发效能智能体平台-技术方案.md`](docs/研发效能智能体平台-技术方案.md)

## 当前进度

- **P0 内核**：FastAPI + LiteLLM(国内模型) + LangGraph 最小 ReAct Agent + SSE 流式 + React(AntD + Redux Toolkit) 控制台 ✅
- **P1 知识答疑 Agent**：pgvector RAG 全链路（入库分块→向量化→检索→带引用流式回答），自定义 LangGraph 工作流（retrieve→generate）✅
- **P2 代码审查 Agent**：GitHub Webhook（验签）+ ARQ 异步队列 + worker 拉 diff/检索团队规范/LLM 审查/回写 PR；另有手动 SSE 流式审查供 UI 体验 ✅
- **P3 告警根因 Agent**：监控告警 Webhook + 多系统**并行**拉取（日志/指标/发布）+ **假设验证循环**（迭代收敛）+ 推送 IM；AIOps 方向 ✅
- **P4 需求拆解 Agent**：PRD/Confluence → LLM 拆任务 → **HITL 人工闸门**（LangGraph interrupt 暂停编辑）→ 写 Jira（未配置则 dry-run）✅
- **P5 代码理解 Agent**：tree-sitter AST 解析 + Neo4j 代码图谱（File/Function/CALLS）+ 代码问答 + **改动影响分析**（反向调用遍历）✅
- **P6 工程化**：成本治理（LiteLLM 回调统计 + 面板）+ Langfuse Eval（LLM-as-judge）+ Docker/K8s 部署 ✅

```
ai-knowledge-agent/
├── backend/
│   ├── Dockerfile            # API + worker 共用镜像
│   └── app/
│       ├── kernel/           # 共享内核：llm / tools / orchestrator / rag / queue / graph / cost
│       ├── agents/           # knowledge_qa(P1)/code_review(P2)/incident_rca(P3)/requirement(P4)/code_understanding(P5)
│       ├── integrations/     # GitHub(P2)/monitoring+logs+notifier(P3)/confluence+jira(P4)
│       ├── workers/          # ARQ worker：review(P2)/rca(P3)/worker(聚合)
│       ├── eval/             # P6 评测：数据集 + LLM-as-judge + CLI runner
│       ├── models/           # Document / Chunk (pgvector)
│       └── gateway/          # 各 Agent 路由 + admin(成本) + webhook
├── frontend/                 # React 18 + TS + Vite + AntD + Redux Toolkit（+ TanStack Query 用于成本面板）
│   ├── Dockerfile + nginx.conf
├── deploy/
│   ├── docker-compose.yml    # PG/pgvector + Redis + Neo4j + Langfuse
│   └── k8s/                  # P6：namespace/configmap/secret/backend/worker/frontend/ingress
└── docs/                     # 技术方案
```

> **P1 跑通前置**：必须先起 PostgreSQL+pgvector（`docker compose -f deploy/docker-compose.yml up -d`），
> 并在 `backend/.env` 配置 `EMBEDDING_MODEL` + 对应厂商 Key（如 `DASHSCOPE_API_KEY`）。
> embedding 维度 `EMBEDDING_DIM` 必须与所选模型匹配，换模型需重建向量表。

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | React 18 + TypeScript + Vite + Ant Design 5 + **Redux Toolkit**（客户端状态）+ TanStack Query（服务端状态） |
| 后端 | Python 3.11 + FastAPI + Pydantic v2 |
| Agent | LangGraph（编排）+ LangChain（工具）+ LiteLLM（模型网关） |
| 模型 | 国内模型：DeepSeek / 通义千问 / 智谱 GLM |
| 数据 | PostgreSQL + pgvector / Redis（P0 预留） |
| 可观测 | Langfuse / structlog |

## 快速开始

### 1. 起依赖（可选，P0 最小闭环可跳过）

```bash
docker compose -f deploy/docker-compose.yml up -d
```

### 2. 启动后端

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env        # 填入 DEEPSEEK_API_KEY
uvicorn app.main:app --reload --port 8000
```

详见 [`backend/README.md`](backend/README.md)。

### 3. 启动前端

```bash
cd frontend
pnpm install   # 或 npm install
pnpm dev       # http://localhost:5173
```

### 4. 体验

- **知识答疑 (P1)**：打开 <http://localhost:5173>，左侧「入库」一篇文档（标题 + 正文），右侧基于它提问，回答会流式输出并标注引用来源 `[n]`。
- **通用对话 (P0)**：切到「通用对话」页，问「现在几点了？再算一下 `12*(3+4)`」，观察 Agent 调用工具（🔧 标签）。

## RAG 链路说明（P1）

```
入库：正文 → RecursiveCharacterTextSplitter 分块 → LiteLLM embedding → 存 pgvector
问答：问题 → embedding → pgvector 余弦距离检索 Top-K → 拼上下文 → LLM 带 [n] 引用流式生成
```

- 检索：`backend/app/kernel/rag/retriever.py`（`Chunk.embedding.cosine_distance`）
- 工作流：`backend/app/agents/knowledge_qa/graph.py`（自定义 LangGraph 两节点 + `stream_mode=["updates","messages"]` 同时推来源与 token）

## 代码审查链路说明（P2）

两条入口共用 `reviewer.py` 的 prompt 构造与规范检索：

```
Webhook（事件驱动）：GitHub PR 事件 → 验签 → ARQ 入队 → worker 拉 diff → 检索团队规范 → LLM 审查 → 回写 PR Review
手动（UI 体验）   ：粘贴 diff → LangGraph(load_rules→review) → SSE 流式返回审查意见
```

- 团队审查规范复用 P1 知识库：在「知识答疑」入库 `source=review-rule` 的规范文档，审查时自动检索。
- 启动异步 worker（P2 Webhook 路径必需）：

```bash
cd backend && source .venv/bin/activate
arq app.workers.review_worker.WorkerSettings
```

- 配置 GitHub Webhook：仓库 Settings → Webhooks，Payload URL 指向 `/api/webhooks/github`，
  Content-Type `application/json`，Secret 与 `.env` 的 `GITHUB_WEBHOOK_SECRET` 一致，事件选 Pull requests。
  本地可用 cloudflared / ngrok 把 8000 端口暴露到公网。

> 异步 worker 已聚合，统一用 `arq app.workers.worker.WorkerSettings` 启动（同时处理 P2 审查与 P3 根因任务）。

## 告警根因链路说明（P3，AIOps）

```
并行：START ─┬─ 拉日志(Loki/mock) ─┐
            ├─ 拉指标(Prometheus/mock) ┼─→ analyze(生成假设) ─→ verify(验证)
            └─ 拉发布(GitHub commits/mock) ┘                       │
                                          ┌──置信度<阈值且未超迭代──┘
                                          └─→ 回 analyze 重新分析
            置信度达标 / 达迭代上限 ─→ conclude(根因报告) ─→ END
```

- **多系统并行编排**：`graph.py` 中 START 扇出 3 个 fetch 节点（写不同 state 键，无冲突），全部完成才汇聚到 analyze。
- **假设验证循环**：`add_conditional_edges("verify", route, ["analyze","conclude"])` 实现回炉，受 `RCA_MAX_ITERATIONS` / `RCA_CONFIDENCE_THRESHOLD` 控制。
- **无监控也能跑**：未配 `PROMETHEUS_URL` / `LOKI_URL` / `DEPLOY_REPO` 时各自用 mock 数据，UI 上完整体验。
- 体验：到「告警根因 (P3)」页用预置示例（HighMemoryUsage）点「开始根因分析」，看并行步骤 + 流式根因报告。

## 代码理解链路说明（P5）

```
索引：遍历仓库 → tree-sitter 解析 → 抽取 Function + CALLS → 写入 Neo4j 图谱
问答：问题提取标识符 → 图谱检索相关函数 → LLM 基于代码上下文流式回答
影响：目标函数 → 沿 CALLS 反向遍历(深度可配) → 找出所有调用者(影响面) + LLM 总结
```

- 图谱：`backend/app/kernel/graph/`（store=Neo4j 读写 / parser=tree-sitter / indexer=遍历入库）
- 支持语言：Python / JS / TS / Java / Go（通用语法树遍历，非逐语言 query）
- 前置：起 Neo4j（compose 已含），`.env` 配 `NEO4J_*`
- 体验：到「代码理解 (P5)」页填仓库名 + 本地路径（如本项目 `backend`）→「构建代码图谱」→ 切「代码问答 / 影响分析」。
  例：索引本项目后问「index_repo 做了什么」，或对 `retrieve` 做影响分析。

## 需求拆解链路说明（P4，HITL）

```
plan：PRD/Confluence → LLM 拆任务 → human_review 节点 interrupt() 暂停（不写库）→ 返回提议 + thread_id
（人工在 UI 编辑任务表）
confirm：Command(resume=编辑后任务) + 同 thread_id → 恢复 → write_jira（未配 Atlassian 则 dry-run）
```

- HITL 核心：`backend/app/agents/requirement/graph.py` 用 `interrupt()` + `MemorySaver` checkpointer 实现「暂停—人工介入—恢复」。
- 两阶段接口：`POST /api/requirement/plan`（暂停态）、`POST /api/requirement/confirm`（确认写入）。
- 体验：到「需求拆解 (P4)」页用预置 PRD 点「拆解任务」→ 编辑任务表 → 「确认并写入 Jira」（无 Atlassian 配置时 dry-run）。

## 工程化说明（P6）

- **成本治理**：`backend/app/kernel/cost.py` 用 LiteLLM 成功回调累计 token/费用；`GET /api/admin/cost`；前端「成本治理 (P6)」页用 **TanStack Query** 轮询展示。
- **Eval 评估**：`python -m app.eval.run_eval` 跑数据集 → 知识答疑 Agent → LLM-as-judge 打分 → 汇总（可选上报 Langfuse）。
- **部署**：`backend/Dockerfile`、`frontend/Dockerfile`(+nginx)、`deploy/k8s/`（见该目录 README）。

## 平台进度（全部完成）

P0 内核 → P1 RAG → P2 事件驱动审查 → P3 AIOps 根因 → P4 HITL 需求拆解 → P5 代码图谱 → P6 工程化。
