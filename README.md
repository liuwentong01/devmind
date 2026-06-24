# DevMind · 研发效能智能体平台

AI 原生的内部研发效能平台。5 个垂直 Agent（代码审查 / 告警根因 / 需求拆解 / 知识答疑 / 代码理解）共享同一套「Agent 内核」。

> 完整技术方案见 [`docs/研发效能智能体平台-技术方案.md`](docs/研发效能智能体平台-技术方案.md)

## 当前进度：P0 内核（最小可跑闭环）

已具备：**FastAPI + LiteLLM(国内模型) + LangGraph 最小 ReAct Agent + SSE 流式 + React(AntD + Redux Toolkit) 控制台**。

```
ai-knowledge-agent/
├── backend/          # Python + FastAPI + LangGraph 内核
├── frontend/         # React 18 + TS + Vite + AntD + Redux Toolkit
├── deploy/           # docker-compose（PG/pgvector + Redis + Langfuse）
└── docs/             # 技术方案
```

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

打开 <http://localhost:5173>，问：「现在几点了？再帮我算一下 `12 * (3 + 4)`」，观察 Agent 流式输出并调用工具（界面会标出 🔧 工具名）。

## 下一步（P1）

知识答疑 Agent：接入 pgvector 做 RAG 全链路（文档入库 → 向量检索 → Rerank → 带引用回答）。
