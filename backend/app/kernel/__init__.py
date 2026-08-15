"""DevMind 与业务无关的智能体内核。

这里集中放置所有智能体共享的基础设施：LLM 网关、LangGraph 编排、受限工具、RAG、
Neo4j 代码图谱、ARQ 队列、成本聚合和需求 HITL 状态存储。业务语义应留在
:mod:`app.agents`，HTTP 校验和事务装配应留在路由/服务层，从而让内核可以独立测试。

典型组合示例::

    model = get_chat_model()
    chunks = await retrieve(session, "如何发布？", top_k=5)
    queue = await get_queue()
    await queue.enqueue_job("review_pr", ..., _job_id=review_job_id(...))

内核中的缓存和连接池是进程级状态，必须在应用 lifespan 结束时关闭。Redis、Neo4j、
PostgreSQL 与模型服务均可能失败，具体模块会说明是 fail-closed 还是 best-effort。当前内核
不是通用 Agent SDK；多租户策略中心、统一模型降级和跨系统事务仍是未来路线。
"""
