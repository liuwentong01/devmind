# DevMind · 研发效能智能体平台

AI 原生的内部研发效能平台。5 个垂直 Agent（代码审查 / 告警根因 / 需求拆解 / 知识答疑 / 代码理解）共享同一套「Agent 内核」。

> 目标技术蓝图（含尚未落地能力）见 [`docs/研发效能智能体平台-技术方案.md`](docs/研发效能智能体平台-技术方案.md)；当前事实与上线门槛以本 README 和生产检查清单为准。

## 当前成熟度

核心业务链路已经形成可运行基线，但这不等于任何环境都已“生产就绪”。代码侧已具备 API/Webhook 鉴权、持久化 HITL、迁移、依赖锁、非 root 镜像、健康探针与安全部署骨架；身份体系、企业 RBAC、托管数据服务、备份恢复、容量验证、SLO/告警和灾备演练仍需结合目标环境完成。

| 能力 | 当前状态 | 上线前仍需验证 |
|---|---|---|
| P0/P1 对话与知识答疑 | 功能链路可运行，REST/SSE 有统一错误边界 | 模型数据合规、检索质量基线、并发与成本预算 |
| P2 代码审查 | Webhook 验签、队列任务与幂等回写已实现 | GitHub App 最小权限、失败队列/重放与仓库级授权 |
| P3 告警根因 | 并行取证和假设验证已实现；mock 仅限本地 | 接入真实 Loki/Prometheus/发布源并做故障演练 |
| P4 需求拆解 | 待确认计划和 Jira 写入进度持久化，可跨副本确认 | Jira 项目权限、审批审计和补偿流程 |
| P5 代码理解 | AST/Neo4j 索引与影响分析可运行 | 只读代码卷、租户隔离、增量索引和大仓容量 |
| 交付基线 | CI、锁文件、容器/K8s 安全骨架已提供 | OIDC/RBAC、镜像签名、备份恢复、SLO 与正式压测 |

生产评审以 [`docs/生产上线检查清单.md`](docs/生产上线检查清单.md) 为准，不能用功能演示结果替代上线验收。

新人阅读源码建议先看 [`docs/源码注释与使用示例索引.md`](docs/源码注释与使用示例索引.md)：它按真实请求链组织文件入口，并说明生成文件、迁移、测试和部署制品的正确使用方式。

```
devmind/
├── backend/
│   ├── Dockerfile            # API + worker 共用的非 root 镜像
│   ├── requirements*.lock    # 带哈希的生产/CI 依赖锁
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
│   ├── docker-compose.yml    # 仅本地开发的依赖服务
│   ├── docker-compose.prod.yml # 无状态应用；外接托管数据服务
│   └── k8s/                  # 迁移/探针/滚动升级/PDB/HPA/网络策略/OIDC 入口骨架
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
| 数据 | PostgreSQL + pgvector / Redis（队列与分布式成本聚合）/ Neo4j |
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
alembic upgrade head        # 首次建库及 schema 变更后执行
uvicorn app.main:app --reload --port 8000
```

详见 [`backend/README.md`](backend/README.md)。

### 3. 启动前端

```bash
cd frontend
npm ci
npm run dev    # http://localhost:5173
```

### 4. 体验

- **知识答疑 (P1)**：打开 <http://localhost:5173>，左侧选择文档用途后入库；“团队代码审查规范”会写入 `source=review-rule`。右侧回答会流式输出并标注引用来源 `[n]`。
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
- **环境隔离**：本地未配置数据源时可用 mock 体验；非本地环境不会伪造证据，缺失或查询失败会明确标为不可用。
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
plan：PRD/Confluence → LLM 拆任务 → PostgreSQL 持久化待确认计划 → 返回提议 + thread_id
（人工在 UI 编辑任务表）
confirm：租约式原子认领 → 逐项幂等写 Jira → 持久化进度与最终结果
```

- HITL 状态保存在 PostgreSQL，`plan` / `confirm` 可落到不同 API 副本；重复确认复用首次结果，中途失败只续写未完成任务。
- 两阶段接口：`POST /api/requirement/plan`（持久化待确认计划）、`POST /api/requirement/confirm`（确认写入）。
- 本地可显式允许 Jira dry-run；非本地环境强制关闭 dry-run，未配置 Atlassian 时会失败而不会伪造成功。

## 工程化说明（P6）

- **成本治理**：LiteLLM 回调写入 Redis，在 API/worker 多副本间原子聚合；价格表随锁定依赖发布，不在进程启动时联网更新。`/api/admin/*` 只接受管理 key。成本页仅在本地开发构建展示，生产运维应通过受控管理通道访问；该统计用于工程预算，不替代供应商账单。
- **Eval 评估**：`python -m app.eval.run_eval` 跑数据集 → 知识答疑 Agent → LLM-as-judge 打分 → 汇总（可选上报 Langfuse）。
- **部署**：`backend/Dockerfile`、`frontend/Dockerfile`(+nginx)、`deploy/k8s/`（见该目录 README）。

## 生产基线与边界

仓库中的部署清单是安全默认的参考基线，不会替代企业环境的身份、数据、网络和运维控制。生产入口必须先接入 OIDC/SSO，并将用户/租户 RBAC、审计、配额与共享服务 API key 分离；PostgreSQL/Redis/Neo4j 必须使用 TLS、非默认凭据和可恢复的托管方案。完整 Go/No-Go 条件见 [`docs/生产上线检查清单.md`](docs/生产上线检查清单.md)。
