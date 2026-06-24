"""评测 CLI：跑数据集 → 知识答疑 Agent → LLM-as-judge 打分 → 汇总（可选上报 Langfuse）。

用法：
    python -m app.eval.run_eval                       # 用默认数据集
    python -m app.eval.run_eval path/to/dataset.json  # 自定义数据集

前置：需先在「知识答疑」入库对应文档（默认数据集对应请假制度示例）。
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
    """跑一次知识答疑 Agent，返回答案。"""
    agent = build_knowledge_agent()
    async with SessionLocal() as session:
        final = await agent.ainvoke({"question": question, "session": session})
    return str(final.get("answer", ""))


def _report_langfuse(items: list[dict]) -> None:
    """可选：把每条评测结果上报 Langfuse。"""
    settings = get_settings()
    if not settings.langfuse_enabled:
        return
    try:
        from langfuse import Langfuse

        lf = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        for it in items:
            trace = lf.trace(name="knowledge-eval", input=it["question"], output=it["answer"])
            trace.score(name="correctness", value=it["score"], comment=it["reason"])
        lf.flush()
        logger.info("langfuse_reported", count=len(items))
    except Exception as exc:  # noqa: BLE001
        logger.warning("langfuse_report_failed", error=str(exc))


async def main() -> None:
    setup_logging()
    dataset_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATASET
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    results = []
    for case in dataset:
        question, reference = case["question"], case["reference"]
        answer = await _run_agent(question)
        verdict = await judge(question, reference, answer)
        results.append(
            {"question": question, "answer": answer, **verdict}
        )
        print(f"[{verdict['score']:.2f}] {question}\n    → {answer[:80]}\n    ({verdict['reason']})")

    avg = sum(r["score"] for r in results) / len(results) if results else 0.0
    print("\n" + "=" * 60)
    print(f"用例数: {len(results)}  平均分: {avg:.3f}")
    print("=" * 60)

    _report_langfuse(results)


if __name__ == "__main__":
    asyncio.run(main())
