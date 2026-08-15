"""离线评测 CLI：数据集 → 知识答疑 Agent → LLM-as-judge → 汇总/可选上报。

用法：
    python -m app.eval.run_eval                       # 用默认数据集
    python -m app.eval.run_eval path/to/dataset.json  # 自定义数据集

前置：需先在「知识答疑」入库对应文档（默认数据集对应请假制度示例）。

数据集是 JSON 数组，每项至少有 ``question`` 与 ``reference``。运行时为每项创建数据库
会话、调用在线模型并打印分数；若配置 Langfuse，则把结果作为 evaluator observation 上报。

生产边界：这是人工触发的离线工具，不应在 API/worker 启动时导入执行。自定义数据集路径
来自受信任操作者，数据集不会自动入库；模型调用会产生真实费用，CI 使用前应设置用例上限、
预算和明确通过阈值。上报失败仅记录告警，不改变本地评测结果。
"""

import asyncio
import json
import sys
from pathlib import Path

from app.agents.knowledge_qa.graph import build_knowledge_agent
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger, setup_logging
from app.eval.judge import judge

logger = get_logger("eval")

DEFAULT_DATASET = Path(__file__).parent / "datasets" / "knowledge_qa.json"


async def _run_agent(question: str) -> str:
    """为单个问题创建会话并运行知识问答 Agent，提取最终文本答案。"""
    agent = build_knowledge_agent()
    async with SessionLocal() as session:
        final = await agent.ainvoke({"question": question, "session": session})
    return str(final.get("answer", ""))


def _report_langfuse(items: list[dict]) -> None:
    """尽力把每条评测结果上报 Langfuse；未配置或失败时不阻断 CLI。"""
    settings = get_settings()
    if not settings.langfuse_enabled:
        return
    try:
        from langfuse import Langfuse

        lf = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_host,
            environment=settings.normalized_app_env,
        )
        for it in items:
            observation = lf.start_observation(
                name="knowledge-eval",
                as_type="evaluator",
                input={"question": it["question"]},
                output={"answer": it["answer"]},
                metadata={"reason": it["reason"]},
            )
            observation.score(
                name="correctness",
                value=it["score"],
                comment=it["reason"],
            )
            observation.end()
        lf.flush()
        logger.info("langfuse_reported", count=len(items))
    except Exception as exc:  # noqa: BLE001
        logger.warning("langfuse_report_failed", error_type=type(exc).__name__)


async def main() -> None:
    """解析命令行数据集路径，串行执行用例并输出汇总。

    当前刻意串行运行，避免离线评测突然耗尽模型/数据库配额；需要并发时应先实现显式的
    并发上限与供应商限流，而不是直接 ``gather`` 全部用例。
    """
    setup_logging()
    dataset_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATASET
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    results = []
    for case in dataset:
        question, reference = case["question"], case["reference"]
        answer = await _run_agent(question)
        verdict = await judge(question, reference, answer)
        results.append({"question": question, "answer": answer, **verdict})
        print(
            f"[{verdict['score']:.2f}] {question}\n    → {answer[:80]}\n    ({verdict['reason']})"
        )

    avg = sum(r["score"] for r in results) / len(results) if results else 0.0
    print("\n" + "=" * 60)
    print(f"用例数: {len(results)}  平均分: {avg:.3f}")
    print("=" * 60)

    _report_langfuse(results)


if __name__ == "__main__":
    asyncio.run(main())
