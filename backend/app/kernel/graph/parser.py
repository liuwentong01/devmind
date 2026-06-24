"""tree-sitter 代码解析：从源码抽取函数定义与函数调用。

采用通用的语法树遍历（而非逐语言写 query），支持 Python / JS / TS / Java / Go。
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


def _text(node: Any, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _walk(node: Any) -> Iterator[Any]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _callee_name(call_node: Any, src: bytes) -> str | None:
    """从一个调用节点中提取被调用函数名。"""
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
    ids = [n for n in _walk(fn) if n.type in ("identifier", "property_identifier", "field_identifier")]
    return _text(ids[-1], src) if ids else None


def parse_source(path: str, source: str, language: str) -> tuple[list[dict], list[dict]]:
    """解析单个文件源码。

    Returns:
        (functions, calls)
        functions: [{id, path, name, start_line, end_line, code}]
        calls: [{caller_id, callee_name}]
    """
    try:
        parser = get_parser(language)
    except Exception as exc:  # noqa: BLE001 - 不支持的语言直接跳过
        logger.warning("parser_unavailable", language=language, error=str(exc))
        return [], []

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
        fid = f"{path}::{name}::{node.start_point[0] + 1}"
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
