# DevMind 后端（P0 内核）

FastAPI + LiteLLM(国内模型) + LangGraph 最小 Agent 循环，提供 SSE 流式对话接口。

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

- 健康检查：<http://localhost:8000/api/health>
- 接口文档：<http://localhost:8000/docs>

### 4. 测试流式对话

```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"现在几点了？再算一下 12*(3+4)"}]}'
```

应能看到 Agent 调用 `get_current_time` / `calculate` 工具并流式返回结果。
