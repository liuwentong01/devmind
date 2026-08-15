"""事故根因分析（RCA）智能体子包。

``graph`` 并行收集日志、指标与近期发布，随后循环执行“提出假设 → 验证假设”，最终生成
带证据降级提示的 Markdown 报告；``analyzer`` 封装 JSON 型模型调用和公共提示词。

典型用法::

    state = await build_rca_agent().ainvoke(
        {"alert": {"alertname": "HighErrorRate", "service": "api"}}
    )
    print(state["conclusion"])

该流程会访问日志、监控、GitHub 与 LLM 服务。任何“根因”都是基于可见证据的辅助判断，
数据源缺失、模型未确认或置信度不足时必须保留警告，不能作为自动定责依据。自动执行回滚、
变更审批与事故工单编排目前不在本包实现范围内。
"""
