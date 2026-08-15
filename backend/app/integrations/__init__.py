"""DevMind 与企业外部系统之间的适配层。

本包只负责把外部系统的 HTTP 协议转换成应用内部易用的 Python 接口，业务编排
仍放在 ``app.agents``、``app.kernel`` 或 ``app.workers`` 中。这样的边界可以让调用方
在测试时替换客户端，而不会真的访问 GitHub、Jira、Confluence、Loki 或 Prometheus。

安全与可靠性约定：

* 凭据统一从 :func:`app.core.config.get_settings` 读取，绝不由请求参数传入；
* 所有响应都要同时限制超时和解压后的实际大小，不能只信 ``Content-Length``；
* 日志不得记录 token、Basic Auth、Webhook URL 或原始异常字符串；
* local 环境允许部分可识别的演示数据，非 local 环境必须 fail-closed，不能伪造证据；
* HTTP 传输层的短暂失败可有限重试，业务层幂等由稳定标签、marker 或队列 job id 保证。

常见用法::

    from app.integrations.github import GitHubClient

    client = GitHubClient()  # 从 Settings 读取 API 地址与 PAT
    diff = await client.get_pull_diff("acme", "payments", 42)

具体请求、响应和异常契约见各模块及公开方法的 docstring。
"""
