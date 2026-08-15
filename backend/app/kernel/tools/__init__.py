"""Agent 可调用工具的安全边界。

当前 ``builtin`` 只暴露时区查询与基于 AST 白名单的四则运算，用于验证 ReAct 工具调用
闭环。工具输入来自模型，必须按不可信数据处理；不能用 ``eval``、shell 或任意文件访问
替代现有受限实现。

典型用法::

    tools = get_builtin_tools()
    result = calculate.invoke({"expression": "12 * (3 + 4)"})

时间工具读取系统时钟，计算工具无外部副作用。GitHub、Jira、监控等真实集成目前通过明确
的客户端和业务工作流调用，并未作为动态通用工具开放；未来接入 MCP 时仍需权限、审计、
超时和审批策略。
"""
