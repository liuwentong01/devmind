"""检索增强生成（RAG）内核。

本包完成 ``文档 → 分块 → Embedding → pgvector`` 的入库链路，以及
``问题 → Query Embedding → 余弦近邻`` 的检索链路。业务智能体负责把检索结果组织成
提示词并展示引用。

典型用法::

    document = await ingest_document(session, "发布手册", markdown, source="manual")
    await session.commit()  # 事务边界由调用方决定
    hits = await retrieve(session, "如何回滚？", top_k=5, source="manual")

Embedding 会访问外部供应商，入库会写 PostgreSQL；维度必须与数据库迁移一致。当前实现
提供向量召回和 source 等值过滤，不包含混合检索、重排序、细粒度 ACL 或自动增量同步，
这些属于后续路线。
"""
