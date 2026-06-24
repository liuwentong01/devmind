"""仓库索引器：遍历仓库源码 → 解析 → 写入 Neo4j 代码图谱。"""

import os
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger
from app.kernel.graph import store
from app.kernel.graph.parser import LANG_BY_EXT, parse_source

logger = get_logger("graph.indexer")

# 跳过的目录
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "dist", "build",
    "__pycache__", ".idea", ".vscode", "target", ".next",
}


async def index_repo(repo: str, root_path: str) -> dict:
    """索引一个本地仓库目录，构建/重建其代码图谱。

    Args:
        repo: 仓库逻辑名（图谱按此隔离）。
        root_path: 本地绝对路径。

    Returns:
        图规模统计 {files, functions, calls}。
    """
    root = Path(root_path).expanduser()
    if not root.is_dir():
        raise ValueError(f"路径不存在或不是目录：{root_path}")

    settings = get_settings()
    await store.ensure_constraints()
    await store.reset_repo(repo)

    indexed_files = 0
    all_calls: list[dict] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            ext = os.path.splitext(fname)[1]
            language = LANG_BY_EXT.get(ext)
            if not language:
                continue
            if indexed_files >= settings.code_index_max_files:
                logger.warning("index_truncated", limit=settings.code_index_max_files)
                break

            fpath = Path(dirpath) / fname
            rel = str(fpath.relative_to(root))
            try:
                source = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            funcs, calls = parse_source(rel, source, language)
            if funcs:
                await store.add_files_and_functions(repo, funcs)
                all_calls.extend(calls)
                indexed_files += 1

    # 函数节点全部写入后，再统一建 CALLS 边（保证被调函数已存在）
    await store.add_calls(repo, all_calls)

    result = await store.stats(repo)
    logger.info("index_done", repo=repo, **result)
    return result
