"""通用 LangGraph 编排入口。

``graph`` 构造平台最小 ReAct Agent，演示模型如何在“思考 → 选择工具 → 观察结果”循环中
使用受限本地工具。垂直业务流程通常在 :mod:`app.agents` 中声明自己的显式状态机。

典型用法::

    result = await build_agent().ainvoke(
        {"messages": [("user", "计算 12 * (3 + 4)")]},
        config={"recursion_limit": 8},
    )

构建结果进程内复用；每次执行可能调用 LLM 和工具。调用方必须设置递归/超时边界。当前
工具集仅含时间与安全算术，外部 MCP 注册中心、审批策略与动态工具授权属于后续路线。
"""
