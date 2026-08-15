"""代码图谱扫描与 Neo4j 原子替换的可靠性回归测试。

图索引必须遵循“完整扫描成功后再替换旧图”的事务边界。测试覆盖仓库级函数身份、符号链接
越界、文件/总字节容量、解析器缺失以及扫描失败时不得触碰已有 Neo4j 数据。

运行示例::

    cd backend
    .venv/bin/python -m unittest tests.test_graph_index_reliability -v

测试使用临时目录和 mock，不要求本机运行 Neo4j。这里的 fail-closed 断言很重要：返回部分
扫描结果会让调用关系图看起来有效却实际缺失，通常比明确失败更难排查。
"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.kernel.graph.indexer import (
    IndexLimitExceeded,
    IndexSourceError,
    _scan_repo,
    index_repo,
)
from app.kernel.graph.parser import ParserUnavailableError, function_id


class GraphIndexReliabilityTests(unittest.TestCase):
    """验证同步仓库扫描的身份隔离、路径安全和资源上限。"""

    def test_function_identity_is_scoped_by_repo(self) -> None:
        """相同路径/函数名在不同仓库中必须生成不同 Neo4j 节点 ID。"""
        first = function_id("repo-a", "src/main.py", "calculate", 1)
        second = function_id("repo-b", "src/main.py", "calculate", 1)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("repo-a::"))
        self.assertTrue(second.startswith("repo-b::"))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_scanner_does_not_follow_file_symlinks_outside_root(self) -> None:
        """指向根目录外的文件软链不得被读取或解析。"""
        with tempfile.TemporaryDirectory() as parent:
            parent_path = Path(parent)
            root = parent_path / "repo"
            root.mkdir()
            outside = parent_path / "outside.py"
            outside.write_text("def outside():\n    return 1\n", encoding="utf-8")
            (root / "linked.py").symlink_to(outside)
            (root / "inside.py").write_text("def inside():\n    return 1\n", encoding="utf-8")

            def fake_parse(repo: str, path: str, source: str, _language: str):
                """从源码内容标记是否错误扫描了 outside 文件。"""
                name = "inside" if "inside" in source else "outside"
                return ([{"id": f"{repo}::{path}::{name}", "name": name}], [])

            with patch("app.kernel.graph.indexer.parse_source", side_effect=fake_parse):
                scan = _scan_repo("repo", root, 20, 1024 * 1024)

        self.assertEqual(scan.files, ["inside.py"])
        self.assertEqual([fn["name"] for fn in scan.functions], ["inside"])

    def test_file_limit_fails_closed_instead_of_returning_partial_scan(self) -> None:
        """文件数超过上限时整体失败，不能用前 N 个文件构造残缺图。"""
        with tempfile.TemporaryDirectory() as root_raw:
            root = Path(root_raw)
            (root / "one.py").write_text("x = 1\n", encoding="utf-8")
            (root / "two.py").write_text("x = 2\n", encoding="utf-8")
            with (
                patch("app.kernel.graph.indexer.parse_source", return_value=([], [])),
                self.assertRaises(IndexLimitExceeded),
            ):
                _scan_repo("repo", root, 1, 1024 * 1024)

    def test_missing_parser_fails_closed(self) -> None:
        """tree-sitter 解析器缺失必须显式失败，不能悄悄生成空图。"""
        with tempfile.TemporaryDirectory() as root_raw:
            root = Path(root_raw)
            (root / "main.py").write_text("x = 1\n", encoding="utf-8")
            with (
                patch(
                    "app.kernel.graph.indexer.parse_source",
                    side_effect=ParserUnavailableError("python unavailable"),
                ),
                self.assertRaises(IndexSourceError),
            ):
                _scan_repo("repo", root, 10, 1024 * 1024)

    def test_aggregate_repository_limit_fails_before_partial_replace(self) -> None:
        """多个小文件累计超过仓库字节上限时也必须拒绝扫描。"""
        with tempfile.TemporaryDirectory() as root_raw:
            root = Path(root_raw)
            (root / "one.py").write_text("x" * 600, encoding="utf-8")
            (root / "two.py").write_text("y" * 600, encoding="utf-8")
            with (
                patch("app.kernel.graph.indexer.parse_source", return_value=([], [])),
                self.assertRaises(IndexLimitExceeded),
            ):
                _scan_repo(
                    "repo",
                    root,
                    10,
                    1024,
                    max_total_bytes=1000,
                )


class AtomicGraphReplacementTests(unittest.IsolatedAsyncioTestCase):
    """验证异步索引编排只在完整扫描成功后调用图存储替换。"""

    async def test_scan_failure_does_not_touch_existing_graph(self) -> None:
        """线程内解析失败时 ``replace_repo`` 不得执行，旧图应保持可用。"""
        settings = SimpleNamespace(
            code_index_max_files=10,
            code_index_max_file_bytes=1024,
            code_index_max_total_bytes=4096,
            code_index_max_functions=100,
            code_index_max_calls=100,
        )
        with tempfile.TemporaryDirectory() as root:
            with (
                patch("app.kernel.graph.indexer.get_settings", return_value=settings),
                patch(
                    "app.kernel.graph.indexer.asyncio.to_thread",
                    AsyncMock(side_effect=RuntimeError("parse failed")),
                ),
                patch("app.kernel.graph.indexer.store.replace_repo", AsyncMock()) as replace,
            ):
                with self.assertRaisesRegex(RuntimeError, "parse failed"):
                    await index_repo("repo", root)
        replace.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
