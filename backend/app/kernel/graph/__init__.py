"""代码知识图谱内核。

数据流为 ``indexer`` 安全遍历本地仓库，``parser`` 用 tree-sitter 抽取函数与调用，
``store`` 在 Neo4j 中原子替换仓库图；代码理解智能体随后读取这些节点回答问题或分析影响。

典型用法::

    stats = await index_repo("devmind", "/srv/repos/devmind")
    callers = await store.find_callers("devmind", "index_repo", depth=2)

索引会读取磁盘并改写指定 ``repo`` 的 Neo4j 子图。允许路径、文件数量/大小等边界由生产
配置限制。当前调用解析是基于语法和名称的轻量实现，不覆盖动态派发、重载解析或跨仓库
符号链接；这些限制必须在向用户展示结果时保留。
"""
