"""代码理解与影响分析智能体子包。

本包消费已经由 :mod:`app.kernel.graph` 写入 Neo4j 的函数/调用图。``graph`` 提供
“定位相关函数 → 基于代码回答”的 LangGraph，``agent`` 还提供调用者/被调用者影响分析。
输入至少包含仓库逻辑名与问题或函数名，输出是图谱命中项和 LLM 总结。

典型用法::

    answer = await build_code_qa_agent().ainvoke(
        {"repo": "devmind", "question": "index_repo 如何保证原子更新？"}
    )
    impact = await analyze_impact("devmind", "index_repo", depth=3)

调用会读取 Neo4j 并调用外部模型，但不会重建图谱。当前 CALLS 关系按同仓库函数名做朴素
解析，同名函数、动态调用和跨服务调用可能产生歧义，因此结果适合作为导航线索而非完整的
编译器级依赖证明；更精确的类型/符号解析是未来路线。
"""
