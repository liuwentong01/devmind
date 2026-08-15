"""DevMind 的业务智能体实现层。

本包位于 HTTP/Worker 入口与 :mod:`app.kernel` 基础能力之间：路由或后台任务负责校验
请求、建立数据库会话并选择智能体；这里的各子包负责把 LLM、RAG、代码图谱和外部系统
组织成可解释的业务工作流。当前包含知识问答、代码审查、代码理解、事故根因分析和需求
拆解五类智能体。

典型调用示例::

    agent = build_knowledge_agent()
    result = await agent.ainvoke(
        {"question": "发布流程是什么？", "session": db_session, "top_k": 5}
    )
    print(result["answer"])

状态与边界：LangGraph 编译结果通常在进程内缓存，但一次请求的状态通过 ``ainvoke`` /
``astream`` 显式传入，不应写入模块全局变量。数据库事务、SSE 生命周期、鉴权和任务重试
由上层持有；外部 I/O 失败可能直接向上传播，或由具体智能体按其安全策略降级。这里记录的
是当前已实现能力；跨智能体统一路由、租户级策略和长期记忆仍属于后续演进方向。
"""
