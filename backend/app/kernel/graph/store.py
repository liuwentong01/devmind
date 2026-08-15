"""Neo4j 代码图谱存储与查询边界。

图模型：
    (File {repo, path})
    (Function {id, repo, path, name, start_line, end_line, code})
    (File)-[:DEFINES]->(Function)
    (Function)-[:CALLS]->(Function)   # 按函数名在同仓库内解析（朴素解析）

架构位置：indexer 把 parser 结果交给本层原子落库；代码理解智能体通过搜索、正向/反向
CALLS 查询读取图。业务隔离键是 ``repo``，Function 全局唯一键是 parser 生成的 ``id``。

典型调用示例::

    await ensure_constraints()
    await replace_repo("devmind", files, functions, calls)
    callers = await find_callers("devmind", "get_queue", depth=3)

模块维护进程级 Neo4j 异步 driver，应用退出时必须调用 ``close_graph``。``replace_repo`` 在
一个事务里先删旧图再分批建新图，任何失败都会由 Neo4j 回滚并保留旧版本。当前 CALLS 仅按
同仓库函数名匹配，可能为所有同名函数建边；查询结果是辅助线索。``add_*``/``reset_repo``
保留给内部增量/维护场景，不具备整仓原子更新保证，生产重建应优先 ``replace_repo``。
"""

from neo4j import AsyncGraphDatabase

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("graph.store")

_driver = None
BATCH_SIZE = 500


def _get_driver():
    """懒加载并返回进程级 Neo4j 异步 driver。

    创建 driver 通常不会立刻完成网络握手，首次查询仍可能抛认证/连接错误。配置在进程启动
    后视为不变；测试若替换 Settings，应先关闭并清空 driver。
    """
    global _driver
    if _driver is None:
        s = get_settings()
        _driver = AsyncGraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))
        logger.info("neo4j_driver_created", uri=s.neo4j_uri)
    return _driver


async def close_graph() -> None:
    """幂等关闭 driver 并清空全局引用，供 lifespan 和测试清理。"""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


async def reset_repo(repo: str) -> None:
    """立即清空指定仓库图。

    这是破坏性维护函数，删除和后续写入不在同一事务；当前生产索引使用 ``replace_repo``，
    避免重建失败后留下空图。调用方不得传入未经授权的仓库名。
    """
    async with _get_driver().session() as session:
        result = await session.run("MATCH (n {repo: $repo}) DETACH DELETE n", repo=repo)
        await result.consume()


async def ensure_constraints() -> None:
    """幂等建立节点唯一约束和 ``(repo, name)`` 查询索引。

    应在首次写图前调用。约束创建若发现历史重复数据会失败并中止索引，这比静默合并错误
    节点安全；生产可在部署/预检阶段提前执行并观察结果。
    """
    async with _get_driver().session() as session:
        result = await session.run(
            "CREATE CONSTRAINT func_id IF NOT EXISTS FOR (f:Function) REQUIRE f.id IS UNIQUE"
        )
        await result.consume()
        result = await session.run(
            "CREATE CONSTRAINT file_repo_path IF NOT EXISTS "
            "FOR (f:File) REQUIRE (f.repo, f.path) IS UNIQUE"
        )
        await result.consume()
        result = await session.run(
            "CREATE INDEX func_repo_name IF NOT EXISTS FOR (f:Function) ON (f.repo, f.name)"
        )
        await result.consume()


async def replace_repo(
    repo: str, files: list[str], functions: list[dict], calls: list[dict]
) -> None:
    """在单个 Neo4j 写事务内完整替换指定仓库图。

    Args:
        repo: 图隔离键。
        files: 仓库相对文件路径。
        functions: parser 生成的函数记录。
        calls: ``caller_id/callee_name`` 调用记录。

    事务内按 ``BATCH_SIZE`` 多次 UNWIND，控制单条 Cypher 参数体积，但整个替换仍一起提交。
    函数边按名称匹配同仓库所有候选且排除自环；这是当前朴素图模型的明确能力边界。
    """
    file_cypher = """
    UNWIND $paths AS path
    CREATE (:File {repo: $repo, path: path})
    """
    node_cypher = """
    UNWIND $rows AS row
    MATCH (file:File {repo: $repo, path: row.path})
    CREATE (fn:Function {
      id: row.id, repo: $repo, path: row.path, name: row.name,
      start_line: row.start_line, end_line: row.end_line, code: row.code
    })
    MERGE (file)-[:DEFINES]->(fn)
    """
    call_cypher = """
    UNWIND $rows AS row
    MATCH (caller:Function {id: row.caller_id})
    MATCH (callee:Function {repo: $repo, name: row.callee_name})
    WHERE callee.id <> caller.id
    MERGE (caller)-[:CALLS]->(callee)
    """

    async def _replace(tx) -> None:  # noqa: ANN001 - Neo4j transaction protocol
        """执行事务内删除与分批重建；仅供 ``execute_write`` 回调。"""
        result = await tx.run("MATCH (n {repo: $repo}) DETACH DELETE n", repo=repo)
        await result.consume()
        for start in range(0, len(files), BATCH_SIZE):
            result = await tx.run(file_cypher, repo=repo, paths=files[start : start + BATCH_SIZE])
            await result.consume()
        for start in range(0, len(functions), BATCH_SIZE):
            result = await tx.run(
                node_cypher, repo=repo, rows=functions[start : start + BATCH_SIZE]
            )
            await result.consume()
        for start in range(0, len(calls), BATCH_SIZE):
            result = await tx.run(call_cypher, repo=repo, rows=calls[start : start + BATCH_SIZE])
            await result.consume()

    async with _get_driver().session() as session:
        await session.execute_write(_replace)


async def add_files_and_functions(repo: str, functions: list[dict]) -> None:
    """批量写入 File / Function 节点及 DEFINES 关系。

    functions: [{id, path, name, start_line, end_line, code}]

    这是非原子整仓替换的低层增量接口：单次调用本身由 driver 提交，但与后续 ``add_calls``
    不共享事务。空输入直接返回。常规重建请使用 ``replace_repo``。
    """
    if not functions:
        return
    cypher = """
    UNWIND $rows AS row
    MERGE (file:File {repo: $repo, path: row.path})
    MERGE (fn:Function {id: row.id})
      SET fn.repo = $repo, fn.path = row.path, fn.name = row.name,
          fn.start_line = row.start_line, fn.end_line = row.end_line, fn.code = row.code
    MERGE (file)-[:DEFINES]->(fn)
    """
    async with _get_driver().session() as session:
        result = await session.run(cypher, repo=repo, rows=functions)
        await result.consume()


async def add_calls(repo: str, calls: list[dict]) -> None:
    """批量写入 CALLS 关系（按被调函数名在同仓库内解析）。

    calls: [{caller_id, callee_name}]

    ``repo`` 只约束 callee；caller 通过全局唯一 ID 命中。空输入无副作用。该接口同样不提供
    与节点写入的跨调用原子性。
    """
    if not calls:
        return
    cypher = """
    UNWIND $rows AS row
    MATCH (caller:Function {id: row.caller_id})
    MATCH (callee:Function {repo: $repo, name: row.callee_name})
    WHERE callee.id <> caller.id
    MERGE (caller)-[:CALLS]->(callee)
    """
    async with _get_driver().session() as session:
        result = await session.run(cypher, repo=repo, rows=calls)
        await result.consume()


async def stats(repo: str) -> dict:
    """返回仓库的 File、Function 和 CALLS 计数，供索引结果/健康诊断展示。"""
    cypher = """
    MATCH (f:File {repo: $repo}) WITH count(DISTINCT f) AS files
    MATCH (fn:Function {repo: $repo}) WITH files, count(fn) AS functions
    OPTIONAL MATCH (:Function {repo: $repo})-[c:CALLS]->(:Function {repo: $repo})
    RETURN files, functions, count(c) AS calls
    """
    async with _get_driver().session() as session:
        rec = await (await session.run(cypher, repo=repo)).single()
        if not rec:
            return {"files": 0, "functions": 0, "calls": 0}
        return {"files": rec["files"], "functions": rec["functions"], "calls": rec["calls"]}


async def find_callers(repo: str, name: str, depth: int = 3) -> list[dict]:
    """反向遍历 1..depth 跳 CALLS，返回最多 100 个潜在调用者。

    ``depth`` 来自上层受限 schema/config，随后转成 int 插入 Cypher；不要把未校验任意文本
    传入。多个同名 target 会合并结果，因此它是保守影响面而非精确符号引用。
    """
    cypher = f"""
    MATCH (target:Function {{repo: $repo, name: $name}})
    MATCH (caller:Function)-[:CALLS*1..{int(depth)}]->(target)
    RETURN DISTINCT caller.name AS name, caller.path AS path,
                    caller.start_line AS start_line
    LIMIT 100
    """
    async with _get_driver().session() as session:
        result = await session.run(cypher, repo=repo, name=name)
        return [dict(rec) async for rec in result]


async def find_callees(repo: str, name: str) -> list[dict]:
    """返回同仓库同名目标函数的一跳被调用者，最多 100 条并去重。"""
    cypher = """
    MATCH (f:Function {repo: $repo, name: $name})-[:CALLS]->(callee:Function)
    RETURN DISTINCT callee.name AS name, callee.path AS path
    LIMIT 100
    """
    async with _get_driver().session() as session:
        result = await session.run(cypher, repo=repo, name=name)
        return [dict(rec) async for rec in result]


async def search_functions(repo: str, keyword: str, limit: int = 10) -> list[dict]:
    """按函数名大小写不敏感包含查询，返回源码与行号供问答定位。

    参数通过 Neo4j 参数绑定，不拼接 keyword；``limit`` 由代码理解层给出小值。当前没有
    全文/语义搜索，问题中必须出现接近真实函数名的标识符才容易命中。
    """
    cypher = """
    MATCH (f:Function {repo: $repo})
    WHERE toLower(f.name) CONTAINS toLower($kw)
    RETURN f.name AS name, f.path AS path, f.code AS code,
           f.start_line AS start_line, f.end_line AS end_line
    LIMIT $limit
    """
    async with _get_driver().session() as session:
        result = await session.run(cypher, repo=repo, kw=keyword, limit=limit)
        return [dict(rec) async for rec in result]
