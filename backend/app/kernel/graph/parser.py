"""tree-sitter 轻量代码解析：抽取函数定义和名称级调用关系。

架构位置：仓库 indexer 对每个受支持文件调用 ``parse_source``，结果经 store 写成 Neo4j 的
``File/Function/DEFINES/CALLS`` 图。这里采用跨语言通用语法树遍历，支持 Python、JS/JSX、
TS/TSX、Java 和 Go，避免为每种语言维护独立 query。

典型调用示例::

    functions, calls = parse_source(
        "demo", "math.py", "def add(a, b):\n    return a + b\n", "python"
    )

函数定义包含稳定 ID、路径、行号和最多 4000 字符源码；调用边只保存 caller ID 与 callee
名字。解析器缺失会 fail-closed，防止用空结果覆盖已有索引。当前不是语义编译器：它不做
类型推断、导入解析、重载/动态派发、跨仓库链接，按名称连接可能把同名函数都视为候选。
这些限制应在影响分析结果中呈现，更精确符号解析属于未来路线。
"""

from collections.abc import Iterator
from typing import Any

from tree_sitter_language_pack import get_parser

from app.core.logging import get_logger

logger = get_logger("graph.parser")

# 扩展名 → tree-sitter 语言名
LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
}

# 函数 / 方法定义节点类型（跨语言）
FUNC_TYPES = {
    "function_definition",
    "function_declaration",
    "method_definition",
    "method_declaration",
    "generator_function_declaration",
}

# 调用节点类型（跨语言）
CALL_TYPES = {"call", "call_expression", "method_invocation"}


class ParserUnavailableError(RuntimeError):
    """运行环境缺少已配置语言的 parser；整次索引不得静默跳过。"""


def function_id(repo: str, path: str, name: str, start_line: int) -> str:
    """生成包含仓库、路径、名称和起始行的稳定 Function 节点 ID。

    起始行使同一文件中的同名局部函数可区分；仓库名前缀避免不同仓库同路径函数在 Neo4j
    唯一约束下碰撞。代码插行会改变 ID，重建采用整仓原子替换，不依赖 ID 跨版本稳定。
    """
    return f"{repo}::{path}::{name}::{start_line}"


def _text(node: Any, src: bytes) -> str:
    """按 tree-sitter 字节区间提取 UTF-8 文本，非法字节使用替换字符。"""
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _walk(node: Any) -> Iterator[Any]:
    """深度优先遍历语法树节点；保持源码顺序以产生确定结果。"""
    yield node
    for child in node.children:
        yield from _walk(child)


def _callee_name(call_node: Any, src: bytes) -> str | None:
    """尽力从跨语言调用节点提取末端被调名称。

    ``obj.method()``、``pkg.Func()`` 只保留 ``method``/``Func``，这是存储层名称级连接的
    既定契约，也正是同名误连的来源。无法识别时返回 ``None``，而不是构造虚假边。
    """
    fn = call_node.child_by_field_name("function") or call_node.child_by_field_name("name")
    if fn is None:
        return None
    if fn.type in ("identifier", "field_identifier", "property_identifier"):
        return _text(fn, src)
    # 形如 obj.method() / pkg.Func()：取属性/方法名
    for field in ("attribute", "property", "name", "field"):
        child = fn.child_by_field_name(field)
        if child is not None:
            return _text(child, src)
    # 兜底：取最后一个标识符
    ids = [
        n for n in _walk(fn) if n.type in ("identifier", "property_identifier", "field_identifier")
    ]
    return _text(ids[-1], src) if ids else None


def parse_source(repo: str, path: str, source: str, language: str) -> tuple[list[dict], list[dict]]:
    """解析单个文件源码。

    Returns:
        (functions, calls)
        functions: [{id, path, name, start_line, end_line, code}]
        calls: [{caller_id, callee_name}]

    Raises:
        ParserUnavailableError: 语言 parser 无法加载。其他 tree-sitter 异常由 indexer 包装为
        ``IndexSourceError``，从而中止整仓替换。

    嵌套函数的调用归属到“包含该调用且字节范围最小”的函数，这比简单选择第一个外层函数
    更符合实际作用域。模块顶层调用没有 caller，因此当前不会形成 CALLS 边。
    """
    try:
        parser = get_parser(language)
    except Exception as exc:  # noqa: BLE001 - 解析器缺失必须让整次索引 fail closed
        logger.warning(
            "parser_unavailable",
            language=language,
            error_type=type(exc).__name__,
        )
        raise ParserUnavailableError(f"parser unavailable: {language}") from exc

    src = source.encode("utf-8")
    tree = parser.parse(src)
    root = tree.root_node

    # 收集函数定义
    funcs: list[dict] = []
    func_ranges: list[tuple[int, int, str]] = []  # (start_byte, end_byte, id)
    for node in _walk(root):
        if node.type not in FUNC_TYPES:
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        name = _text(name_node, src)
        # Function.id 上有全局唯一约束，必须包含 repo；否则两个仓库的同路径函数会
        # MERGE 成同一节点，后一次索引还会覆盖前一仓库的 repo 属性。
        fid = function_id(repo, path, name, node.start_point[0] + 1)
        code = _text(node, src)
        funcs.append(
            {
                "id": fid,
                "path": path,
                "name": name,
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "code": code[:4000],  # 截断超长函数体
            }
        )
        func_ranges.append((node.start_byte, node.end_byte, fid))

    # 收集调用，并归属到最近的外层函数
    calls: list[dict] = []
    for node in _walk(root):
        if node.type not in CALL_TYPES:
            continue
        callee = _callee_name(node, src)
        if not callee:
            continue
        # 找到包含该调用、且范围最小的函数
        enclosing = None
        best = None
        for start, end, fid in func_ranges:
            if start <= node.start_byte and node.end_byte <= end:
                size = end - start
                if best is None or size < best:
                    best, enclosing = size, fid
        if enclosing:
            calls.append({"caller_id": enclosing, "callee_name": callee})

    return funcs, calls
