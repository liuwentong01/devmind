"""文本分块：把长文档切成适合向量化与检索的小块。"""

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import get_settings


def split_text(text: str) -> list[str]:
    """按配置的块大小 / 重叠把文本递归切分。

    优先在段落、句子边界切分，尽量保持语义完整。
    """
    settings = get_settings()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
    )
    return [chunk.strip() for chunk in splitter.split_text(text) if chunk.strip()]
