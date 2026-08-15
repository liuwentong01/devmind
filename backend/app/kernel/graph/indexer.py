"""安全仓库索引器：磁盘遍历 → tree-sitter 解析 → Neo4j 原子替换。

架构位置：代码索引 API/任务在完成路径授权后调用 ``index_repo``；本模块只读取允许的本地
根目录，把源文件交给 :mod:`app.kernel.graph.parser`，全部成功后再通过 ``store`` 替换该
``repo`` 的图。代码问答和影响分析消费最终图谱。

典型调用示例::

    stats = await index_repo("devmind", "/srv/repos/devmind")
    # => {"files": 123, "functions": 900, "calls": 2400}

为什么“先完整扫描，后原子替换”：若逐文件删除/写入，解析到坏文件或 Neo4j 中途失败会让
线上查询看到残缺图。这里扫描阶段 fail-closed，写入阶段单事务执行，失败保留旧索引。
扫描被放入线程池，避免同步文件 I/O 和 tree-sitter CPU 工作阻塞 FastAPI 事件循环。

安全/能力边界：符号链接被跳过且文件用 ``O_NOFOLLOW`` 打开，文件数、单文件/总字节、函数
和调用边均有确定上限。调用方仍须先把 ``root_path`` 限制在配置允许根目录；本模块不做
租户授权。当前索引只支持映射表中的语言、读取 UTF-8（非法字节替换），不解析 git 分支、
构建产物或跨仓库依赖；同仓库并发重建仍是“最后完成者生效”，分布式索引锁属未来路线。
"""

import asyncio
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger
from app.kernel.graph import store
from app.kernel.graph.parser import LANG_BY_EXT, parse_source

logger = get_logger("graph.indexer")

# 跳过的目录
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".idea",
    ".vscode",
    "target",
    ".next",
}


class IndexLimitExceeded(ValueError):
    """仓库超过文件、字节、函数或调用边的确定性安全上限。"""


class IndexSourceError(RuntimeError):
    """受支持源码无法完整读取或解析；整次重建必须失败并保留旧图。"""


@dataclass(frozen=True)
class ScanResult:
    """扫描阶段的内存快照，只有完整成功后才能交给存储层。

    ``files`` 是仓库相对路径；``functions`` 和 ``calls`` 遵循 parser/store 之间的内部字典
    契约。dataclass 冻结只防止字段重新赋值，内部列表仍应视为只读。
    """

    files: list[str]
    functions: list[dict]
    calls: list[dict]


def _scan_repo(
    repo: str,
    root: Path,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int = 64 * 1024 * 1024,
    max_functions: int = 50_000,
    max_calls: int = 200_000,
) -> ScanResult:
    """同步、确定性地扫描和解析仓库；由 ``index_repo`` 在线程池调用。

    目录和文件名排序使相同快照产生稳定遍历顺序，便于测试和故障复现。遇到任何受支持
    文件读/解析失败就抛错，不用残缺结果替换旧图。``max_*`` 参数必须来自受信配置。
    """
    functions: list[dict] = []
    calls: list[dict] = []
    files: list[str] = []
    total_bytes = 0

    for dirpath, dirnames, filenames in os.walk(root):
        # 原地修改 dirnames 才能阻止 os.walk 下钻；同时拒绝目录 symlink，避免逃逸 root。
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink()
        )
        for fname in sorted(filenames):
            ext = os.path.splitext(fname)[1]
            language = LANG_BY_EXT.get(ext)
            if not language:
                continue
            if len(files) >= max_files:
                raise IndexLimitExceeded(f"repository exceeds {max_files} source files")

            fpath = Path(dirpath) / fname
            try:
                if fpath.is_symlink():
                    logger.warning("index_symlink_skipped", path=str(fpath))
                    continue
                # 先判定再用 O_NOFOLLOW 打开，缩小检查与使用之间（TOCTOU）被替换成 symlink
                # 的窗口；不支持 O_NOFOLLOW 的平台仍依赖前置检查和上层允许根目录约束。
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                with os.fdopen(os.open(fpath, flags), "rb") as source_file:
                    file_stat = os.fstat(source_file.fileno())
                    if not stat.S_ISREG(file_stat.st_mode):
                        continue
                    if file_stat.st_size > max_file_bytes:
                        raise IndexLimitExceeded(
                            f"source file exceeds {max_file_bytes} bytes: {fpath}"
                        )
                    total_bytes += file_stat.st_size
                    if total_bytes > max_total_bytes:
                        raise IndexLimitExceeded(
                            f"repository source bytes exceed {max_total_bytes} bytes"
                        )
                    source_bytes = source_file.read(max_file_bytes + 1)
                if len(source_bytes) > max_file_bytes:
                    raise IndexLimitExceeded(f"source file exceeds {max_file_bytes} bytes: {fpath}")
                # 用替换字符保留可解析的其余源码；磁盘读取/大小不完整则仍 fail-closed。
                source = source_bytes.decode("utf-8", errors="replace")
            except OSError as exc:
                raise IndexSourceError(f"source file unreadable: {fpath}") from exc

            rel = str(fpath.relative_to(root))
            try:
                funcs, file_calls = parse_source(repo, rel, source, language)
            except Exception as exc:
                raise IndexSourceError(f"source file parse failed: {rel}") from exc
            if len(functions) + len(funcs) > max_functions:
                raise IndexLimitExceeded(f"repository exceeds {max_functions} functions")
            if len(calls) + len(file_calls) > max_calls:
                raise IndexLimitExceeded(f"repository exceeds {max_calls} call edges")
            files.append(rel)
            functions.extend(funcs)
            calls.extend(file_calls)

    return ScanResult(files=files, functions=functions, calls=calls)


async def index_repo(repo: str, root_path: str) -> dict:
    """索引一个本地仓库目录，构建/重建其代码图谱。

    Args:
        repo: 仓库逻辑名（图谱按此隔离）。
        root_path: 本地绝对路径。

    Returns:
        图规模统计 {files, functions, calls}。

    Raises:
        ValueError: 根路径不是目录。
        IndexLimitExceeded: 仓库越过配置容量上限。
        IndexSourceError: 任一支持文件无法完整读取或解析。
        neo4j.exceptions.Neo4jError: 约束或原子写入失败。

    ``root_path`` 的业务授权由调用方执行；直接从内部代码调用本函数时也必须遵守配置中的
    ``code_allowed_roots``，不能把用户输入路径未经校验传入。
    """
    root = Path(root_path).expanduser()
    if not root.is_dir():
        raise ValueError(f"路径不存在或不是目录：{root_path}")

    settings = get_settings()
    scan = await asyncio.to_thread(
        _scan_repo,
        repo,
        root,
        settings.code_index_max_files,
        settings.code_index_max_file_bytes,
        settings.code_index_max_total_bytes,
        settings.code_index_max_functions,
        settings.code_index_max_calls,
    )

    # 只有完整解析成功后才替换旧图；Neo4j 单事务保证写入失败时旧索引仍可查询。
    await store.ensure_constraints()
    await store.replace_repo(repo, scan.files, scan.functions, scan.calls)

    result = await store.stats(repo)
    logger.info("index_done", repo=repo, **result)
    return result
