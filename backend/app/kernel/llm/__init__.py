"""统一 LLM 接入层。

当前由 ``gateway.get_chat_model`` 把配置中的模型名、密钥、超时、重试和输出上限转换为
LangChain/LangGraph 可使用的 ``ChatLiteLLM``。业务智能体不应直接读取供应商密钥。

典型用法::

    model = get_chat_model(temperature=0)
    response = await model.ainvoke([HumanMessage("解释这段代码")])

返回对象在进程内按参数缓存，调用本身会产生外部网络请求、token 成本和可观测回调。
当前实现提供统一适配但尚未实现按场景路由、熔断/供应商降级或租户配额；这些是未来路线，
不能把 LiteLLM 的 SDK 重试误认为完整的高可用策略。
"""
