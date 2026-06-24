"""代码图谱存储：基于 Neo4j。

图模型：
    (File {repo, path})
    (Function {id, repo, path, name, start_line, end_line, code})
    (File)-[:DEFINES]->(Function)
    (Function)-[:CALLS]->(Function)   # 按函数名在同仓库内解析（朴素解析）

影响分析即沿 CALLS 反向遍历找调用者。
"""

from neo4j import AsyncGraphDatabase

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("graph.store")

_driver = None


def _get_driver():
    """懒加载 Neo4j 异步 driver（单例）。"""
    global _driver
    if _driver is None:
        s = get_settings()
        _driver = AsyncGraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))
        logger.info("neo4j_driver_created", uri=s.neo4j_uri)
    return _driver


async def close_graph() -> None:
    """关闭 driver（应用退出时调用）。"""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


async def reset_repo(repo: str) -> None:
    """清空某仓库的全部图数据（重新索引前调用）。"""
    async with _get_driver().session() as session:
        await session.run(
            "MATCH (n {repo: $repo}) DETACH DELETE n", repo=repo
        )


async def ensure_constraints() -> None:
    """建立唯一约束 / 索引，保证幂等写入与查询性能。"""
    async with _get_driver().session() as session:
        await session.run(
            "CREATE CONSTRAINT func_id IF NOT EXISTS "
            "FOR (f:Function) REQUIRE f.id IS UNIQUE"
        )
        await session.run(
            "CREATE INDEX func_repo_name IF NOT EXISTS "
            "FOR (f:Function) ON (f.repo, f.name)"
        )


async def add_files_and_functions(repo: str, functions: list[dict]) -> None:
    """批量写入 File / Function 节点及 DEFINES 关系。

    functions: [{id, path, name, start_line, end_line, code}]
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
        await session.run(cypher, repo=repo, rows=functions)


async def add_calls(repo: str, calls: list[dict]) -> None:
    """批量写入 CALLS 关系（按被调函数名在同仓库内解析）。

    calls: [{caller_id, callee_name}]
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
        await session.run(cypher, repo=repo, rows=calls)


async def stats(repo: str) -> dict:
    """返回某仓库的图规模统计。"""
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
    """反向遍历调用图，找出（传递地）调用了目标函数的函数 —— 即改动影响面。"""
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
    """找出目标函数直接调用的函数。"""
    cypher = """
    MATCH (f:Function {repo: $repo, name: $name})-[:CALLS]->(callee:Function)
    RETURN DISTINCT callee.name AS name, callee.path AS path
    LIMIT 100
    """
    async with _get_driver().session() as session:
        result = await session.run(cypher, repo=repo, name=name)
        return [dict(rec) async for rec in result]


async def search_functions(repo: str, keyword: str, limit: int = 10) -> list[dict]:
    """按名称关键字模糊检索函数（供问答时定位上下文）。"""
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
