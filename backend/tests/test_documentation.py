"""源码说明覆盖的回归测试。

本文件把本轮约定固化为 CI 门禁：手写 Python 模块必须先说明自己解决什么问题；生产源码
和迁移中的公开类、函数、方法还必须给出可检索的 docstring。它只检查“说明存在”，不会
用字数代替人工评审，也不会扫描 ``script.py.mako``、JSON、lock 等生成/机器格式。

运行示例::

    cd backend
    python -m unittest tests.test_documentation -v
"""

from __future__ import annotations

import ast
import unittest
from collections.abc import Iterator
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOTS = (
    BACKEND_ROOT / "app",
    BACKEND_ROOT / "alembic",
    BACKEND_ROOT / "tests",
)
PUBLIC_API_ROOTS = (BACKEND_ROOT / "app", BACKEND_ROOT / "alembic")


def _python_files(roots: tuple[Path, ...]) -> Iterator[Path]:
    """按稳定顺序枚举手写 Python 文件，忽略缓存目录。"""
    for root in roots:
        yield from sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _public_definitions(
    tree: ast.Module,
) -> Iterator[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]:
    """枚举模块顶层公开定义及公开类成员，不把局部回调误当公共 API。"""
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                yield node
            if isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(
                        member, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ) and not member.name.startswith("_"):
                        yield member


class DocumentationCoverageTest(unittest.TestCase):
    """防止新模块或公共入口绕过仓库的源码说明约定。"""

    def test_every_handwritten_python_module_has_docstring(self) -> None:
        """所有手写 Python 文件都应有非空模块 docstring。"""
        missing: list[str] = []
        for path in _python_files(MODULE_ROOTS):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if not ast.get_docstring(tree, clean=True):
                missing.append(str(path.relative_to(BACKEND_ROOT)))
        self.assertEqual([], missing, f"缺少模块 docstring: {missing}")

    def test_public_production_definitions_have_docstrings(self) -> None:
        """应用与迁移的顶层公开定义/公开方法都应说明其契约。"""
        missing: list[str] = []
        for path in _python_files(PUBLIC_API_ROOTS):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in _public_definitions(tree):
                if not ast.get_docstring(node, clean=True):
                    relative = path.relative_to(BACKEND_ROOT)
                    missing.append(f"{relative}:{node.lineno}:{node.name}")
        self.assertEqual([], missing, f"缺少公开定义 docstring: {missing}")


if __name__ == "__main__":
    unittest.main()
