# DevMind · 研发效能智能体平台

AI 原生的内部研发效能平台。5 个垂直 Agent（代码审查 / 告警根因 / 需求拆解 / 知识答疑 / 代码理解）共享同一套「Agent 内核」。

> 完整技术方案见 [`docs/研发效能智能体平台-技术方案.md`](docs/研发效能智能体平台-技术方案.md)

## 当前进度

- **P0 内核**：FastAPI + LiteLLM(国内模型) + LangGraph 最小 ReAct Agent + SSE 流式 + React(AntD + Redux Toolkit) 控制台 ✅
- **P1 知识答疑 Agent**：pgvector RAG 全链路（入库分块→向量化→检索→带引用流式回答），自定义 LangGraph 工作流（retrieve→generate）✅
- **P2 代码审查 Agent**：GitHub Webhook（验签）+ ARQ 异步队列 + worker 拉 diff/检索团队规范/LLM 审查/回写 PR；另有手动 SSE 流式审查供 UI 体验 ✅

```
ai-knowledge-agent/
├── backend/
│   └── app/
│       ├── kernel/           # 共享内核：llm 网关 / tools / orchestrator / rag / queue
│       ├── agents/           # 垂直 Agent：knowledge_qa(P1) / code_review(P2)
│       ├── integrations/     # GitHub 客户端 (P2)
│       ├── workers/          # ARQ worker：review_worker (P2)
│       ├── models/           # Document / Chunk (pgvector)
│       └── gateway/          # routes(P0) / knowledge(P1) / review+webhook(P2)
├── frontend/                 # React 18 + TS + Vite + AntD + Redux Toolkit
├── deploy/                   # docker-compose（PG/pgvector + Redis + Langfuse）
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

## 下一步（P3）

告警根因 Agent：监控告警 Webhook + 多系统并行编排 + 假设验证循环（AIOps）。
