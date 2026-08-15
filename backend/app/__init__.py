"""DevMind 后端应用包的最早期启动配置。

调用链：Python 导入任意 ``app.*`` 模块时会先执行这里，再加载配置、模型和路由。
目前唯一职责是让 LiteLLM 使用随依赖发布的本地价格表，例如执行
``python -c "import app; print('bootstrap ok')"`` 即会应用该默认值。

生产边界：这里必须保持轻量、确定且无网络/数据库副作用；进程环境可在导入前显式覆盖
该默认值。真实的资源初始化与关闭统一由 ``app.main.lifespan`` 管理。
"""

import os

# LiteLLM 默认在 import 时联网拉取价格表。企业运行时使用随锁定依赖发布的本地副本，
# 避免启动依赖公网和未经发布流程审阅的远程数据；显式进程环境变量仍可覆盖此默认值。
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
