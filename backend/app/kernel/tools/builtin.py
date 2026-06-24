"""内置示例工具：用于在 P0 演示 Agent 的「工具调用」能力。

后续真正的能力（GitHub / Jira / Confluence / 监控）会以 MCP Server 形式
注册进工具注册中心，这里先放两个无副作用的本地工具跑通闭环。
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool


@tool
def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """获取指定时区的当前时间。

    Args:
        timezone: IANA 时区名，例如 "Asia/Shanghai"、"UTC"。
    """
    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception:  # noqa: BLE001 - 非法时区时回退到上海
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        timezone = "Asia/Shanghai"
    return f"{timezone} 当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"


@tool
def calculate(expression: str) -> str:
    """计算一个简单的数学表达式（仅支持 + - * / ( ) 和数字）。

    Args:
        expression: 形如 "12 * (3 + 4)" 的算式。
    """
    allowed = set("0123456789+-*/(). ")
    if not set(expression) <= allowed:
        return "表达式包含不支持的字符，仅允许数字与 + - * / ( ) "
    try:
        # 限定内置环境，避免任意代码执行
        result = eval(expression, {"__builtins__": {}}, {})  # noqa: S307
        return f"{expression} = {result}"
    except Exception as exc:  # noqa: BLE001
        return f"计算失败：{exc}"


def get_builtin_tools() -> list:
    """返回 P0 内置工具列表。"""
    return [get_current_time, calculate]
