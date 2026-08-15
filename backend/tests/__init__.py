"""DevMind 后端回归测试包。

测试以标准库 ``unittest`` 编写，同时可被 pytest 发现。完整运行示例::

    cd backend
    LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python -m unittest discover -s tests -v

各 ``test_*`` 模块按安全、RAG、图索引、队列、需求任务和可观测性边界分组。外部 HTTP、
Redis、数据库与模型调用均使用 fake/mock，测试失败通常表示本地不变量被破坏，而不是外部
服务临时不可用；真实服务联调仍应在预生产环境单独执行。
"""
