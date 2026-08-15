"""SQLAlchemy ORM 模型注册包。

当前包含知识库的 ``Document``/``Chunk`` 和需求人工确认的 ``RequirementPlan``。
``app.core.db.init_db`` 与 Alembic 环境会显式导入这些模块，使表元数据注册到 ``Base``；
业务代码可用 ``from app.models.knowledge import Document`` 导入具体模型。

生产边界：修改模型不等于修改生产数据库，任何表结构变化都必须提供并验证 Alembic 迁移；
不要在本包初始化时连接数据库或执行 DDL。
"""
