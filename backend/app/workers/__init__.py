"""DevMind 的 ARQ 后台任务入口包。

HTTP 路由只负责验证请求并把稳定参数写入 Redis 队列，耗时的 LLM、GitHub、Jira 和通知
操作由这里的 worker 执行。生产通常启动聚合入口::

    arq app.workers.worker.WorkerSettings

也可按工作负载独立扩缩容::

    arq app.workers.review_worker.WorkerSettings
    arq app.workers.rca_worker.WorkerSettings

任务采用 ARQ 的“至少一次”投递语义：进程崩溃、网络超时或显式异常都可能导致重试，
因此外部副作用必须使用 GitHub marker、Jira 稳定 label 或确定性 job id 做幂等保护。
每个入口在消费前初始化并验证数据库、安装成本追踪，在关闭时依次 flush 可观测数据、
关闭 Redis 连接和数据库连接。不要把业务任务直接写进本文件；应放到对应 worker 模块并
在聚合 ``WorkerSettings`` 中注册。
"""
