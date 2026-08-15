"""RAG 文档分块：把长文本切成适合 Embedding 与引用的语义片段。

架构位置：``retriever.ingest_document`` 在调用 Embedding 之前使用本模块。输入是已经过 API
大小校验的纯文本，输出是不含空白块的字符串列表。

典型调用示例::

    chunks = split_text("第一段。\n\n第二段包含更多说明。")

函数本身无 I/O 和持久化副作用。递归 splitter 优先段落、换行和句末边界，只有找不到更好
边界时才退化到空字符串级切分；重叠窗口用于减少答案落在边界上的语义损失，但会增加向量
数量和成本。当前按字符近似长度，不是模型 tokenizer 精确计数，也没有 Markdown/代码专用
结构解析；这属于未来检索质量优化。
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import get_settings


def split_text(text: str) -> list[str]:
    """按配置的块大小 / 重叠把文本递归切分。

    优先在段落、句子边界切分，尽量保持语义完整。

    Returns:
        保序、去除首尾空白且不含空字符串的切片列表。空白输入返回空列表，调用方通常会
        将其视为不可入库文档。

    ``rag_chunk_overlap`` 必须小于 ``rag_chunk_size``，该约束由 Settings 启动校验保证。
    """
    settings = get_settings()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
    )
    return [chunk.strip() for chunk in splitter.split_text(text) if chunk.strip()]
