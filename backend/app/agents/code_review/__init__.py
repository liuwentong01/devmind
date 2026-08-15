"""代码审查智能体子包。

``graph`` 定义“检索团队规范 → 调用模型审查”的交互式 LangGraph；``reviewer`` 提供
共享的规范检索、diff 裁剪与提示词构造，因而也能被 GitHub Webhook 的 ARQ worker
复用。输入是可信边界外的 PR diff，输出是 Markdown 审查意见；团队规范来自 PostgreSQL
中的向量知识库。

典型用法::

    agent = build_review_agent()
    result = await agent.ainvoke({"diff": patch_text, "session": db_session})
    print(result["review"])

该包只生成意见，不负责拉取 PR、校验 head SHA、幂等回写评论或提交事务；这些副作用由
路由/worker 与 GitHub 集成层控制。当前审查基于展示出来的 diff 和召回规范，不等价于
完整静态分析、测试执行或安全扫描。
"""
