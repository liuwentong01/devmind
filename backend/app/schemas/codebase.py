"""代码索引、问答和影响分析 API 的数据契约。

调用链：``/api/codebase/index`` 使用 ``IndexRequest``，随后问答/影响分析以相同 ``repo``
隔离图数据。例如 ``{"repo":"devmind","path":"/srv/repos/devmind"}`` 可请求建索引，
``{"repo":"devmind","question":"鉴权在哪里？"}`` 可发起 SSE 问答。

生产边界：Schema 仅阻止控制字符和异常长度；路径是否位于允许根目录由
``resolve_code_index_path`` 解析真实路径后决定，不能把这里的字符串校验视为文件授权。
"""

from pydantic import BaseModel, Field

from app.schemas.base import StrictRequestModel

_REPO_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
_SAFE_TEXT_PATTERN = r"^[^\x00-\x1f\x7f]+$"


class IndexRequest(StrictRequestModel):
    """索引一个本地仓库目录。"""

    repo: str = Field(description="仓库逻辑名（图谱按此隔离）", pattern=_REPO_PATTERN)
    path: str = Field(
        description="本地仓库绝对路径",
        min_length=1,
        max_length=4096,
        pattern=_SAFE_TEXT_PATTERN,
    )


class IndexResponse(BaseModel):
    """索引结果（图规模统计）。"""

    repo: str
    files: int
    functions: int
    calls: int


class CodeQueryRequest(StrictRequestModel):
    """代码库问答。"""

    repo: str = Field(description="仓库逻辑名", pattern=_REPO_PATTERN)
    question: str = Field(description="对代码库的问题", min_length=1, max_length=8000)


class ImpactRequest(StrictRequestModel):
    """影响分析。"""

    repo: str = Field(description="仓库逻辑名", pattern=_REPO_PATTERN)
    function: str = Field(
        description="目标函数名",
        min_length=1,
        max_length=256,
        pattern=_SAFE_TEXT_PATTERN,
    )
    depth: int = Field(default=3, ge=1, le=5, description="反向调用遍历深度")
