"""需求拆解与人工确认（HITL）智能体子包。

当前 LangGraph 只把 PRD 提议为结构化任务；计划持久化、租约认领和逐条 checkpoint 位于
:mod:`app.kernel.requirement_store`，确认后写 Jira 的副作用由 ``write_jira_tasks`` 执行。
这种两阶段设计让提议与确认可以落在不同 API 副本，并避免把人工等待状态存在进程内。

典型用法::

    proposed = await build_requirement_agent().ainvoke({"prd": prd_text})
    # 上层先持久化 proposed["tasks"]，待用户确认并成功认领租约后：
    result = await write_jira_tasks(
        approved_tasks, idempotency_prefix=thread_id, on_created=save_checkpoint
    )

本包会调用 LLM，并可能在 Jira 创建 Issue。调用方必须先校验任务结构、持有有效租约并在
失败时释放租约。标签幂等与本地 checkpoint 降低重复创建概率，但外部系统超时后的结果
仍需人工核对；复杂依赖排程与 Jira 工作流配置自动发现属于后续能力。
"""
