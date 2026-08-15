"""代码理解 API：仓库索引、基于图谱的 SSE 问答和影响分析。

调用链：先 ``POST /api/codebase/index`` 校验真实路径并把语法图原子写入 Neo4j，再以同一
``repo`` 调用 ``/query/stream`` 或 ``/impact``。索引示例：
``curl -X POST -H 'Content-Type: application/json' -d
'{"repo":"devmind","path":"/srv/repos/devmind"}' http://localhost:8000/api/codebase/index``。

生产边界：服务进程只能读取 ``CODE_INDEX_ALLOWED_ROOTS`` 下的代码；图谱以 repo 隔离，
但当前不等同于终端用户授权。索引是有容量上限的同步重任务，大仓库应改由受限 worker 执行。
Neo4j/LLM 故障统一返回脱敏 503，路径和源码不得写入错误响应或普通访问日志。
"""

import json
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.agents.code_understanding.agent import analyze_impact
from app.agents.code_understanding.graph import build_code_qa_agent
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import PathAccessError, resolve_code_index_path
from app.gateway.sse import stream_response
from app.kernel.graph.indexer import IndexLimitExceeded, index_repo
from app.schemas.codebase import (
    CodeQueryRequest,
    ImpactRequest,
    IndexRequest,
    IndexResponse,
)

logger = get_logger("gateway.codebase")
router = APIRouter(prefix="/codebase", tags=["codebase"])


@router.post("/index", response_model=IndexResponse)
async def index(
    req: IndexRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> IndexResponse:
    """授权并索引本地仓库目录，返回文件/函数/调用边数量。"""
    try:
        resolved_path = resolve_code_index_path(req.path, settings)
    except PathAccessError:
        logger.warning("code_index_path_rejected", repo=req.repo)
        raise HTTPException(status_code=403, detail="索引路径不允许或不可访问") from None
    try:
        stats = await index_repo(req.repo, str(resolved_path))
    except IndexLimitExceeded:
        logger.warning("code_index_limit_exceeded", repo=req.repo)
        raise HTTPException(status_code=413, detail="代码库超过索引容量限制") from None
    except ValueError:
        raise HTTPException(status_code=400, detail="索引路径无效") from None
    except Exception:  # noqa: BLE001 - Neo4j 不可用等
        logger.exception("code_index_failed", repo=req.repo)
        raise HTTPException(status_code=503, detail="代码索引服务暂不可用") from None
    return IndexResponse(repo=req.repo, **stats)


async def _stream_qa(repo: str, question: str) -> AsyncGenerator[dict, None]:
    """把代码问答 LangGraph 的 update/message 双流转换为 SSE 事件。

    ``context`` 事件只暴露相关文件路径，``token`` 只接受 answer 节点的消息，避免把中间
    工具/提示内容误发给客户端；最后总是发送 ``done``，异常则发送固定 ``error``。
    """
    agent = build_code_qa_agent()
    inputs = {"repo": repo, "question": question}
    try:
        async for mode, data in agent.astream(inputs, stream_mode=["updates", "messages"]):
            if mode == "updates":
                located = data.get("locate")
                if located and "context" in located:
                    files = sorted({c["path"] for c in located["context"]})
                    yield {
                        "event": "context",
                        "data": json.dumps({"files": files}, ensure_ascii=False),
                    }
            elif mode == "messages":
                msg_chunk, metadata = data
                if metadata.get("langgraph_node") != "answer":
                    continue
                text = getattr(msg_chunk, "content", "") or ""
                if text:
                    yield {"event": "token", "data": text}
        yield {"event": "done", "data": "[DONE]"}
    except Exception:  # noqa: BLE001
        logger.exception("code_qa_stream_error")
        yield {
            "event": "error",
            "data": json.dumps({"message": "代码问答失败，请稍后重试"}, ensure_ascii=False),
        }


@router.post("/query/stream")
async def query_stream(req: CodeQueryRequest) -> EventSourceResponse:
    """代码库问答：先发 ``context`` 文件列表，再发 ``token`` 与 ``done``。"""
    return stream_response(_stream_qa(req.repo, req.question))


@router.post("/impact")
async def impact(req: ImpactRequest) -> dict:
    """按限定深度查询调用者/被调用者，并生成 LLM 影响总结。"""
    try:
        return await analyze_impact(req.repo, req.function, depth=req.depth)
    except Exception:  # noqa: BLE001
        logger.exception("code_impact_failed", repo=req.repo)
        raise HTTPException(status_code=503, detail="影响分析服务暂不可用") from None
