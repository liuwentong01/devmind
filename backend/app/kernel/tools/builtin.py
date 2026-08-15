"""ReAct Agent 的内置只读工具与安全算术解释器。

架构位置：通用编排器把 ``get_builtin_tools`` 返回的工具绑定给模型。工具参数由模型生成，
因此必须按攻击者可控输入处理。本模块只开放当前时间和四则运算，两者都没有外部系统写入
副作用。

典型调用示例::

    # ``@tool`` 包装后通过 invoke 调用，而非直接依赖内部实现。
    now = get_current_time.invoke({"timezone": "UTC"})
    answer = calculate.invoke({"expression": "12 * (3 + 4)"})

为什么不使用 ``eval``：即使先用正则过滤，语言执行器仍会引入对象访问、实现差异与资源
耗尽风险。这里先用字符白名单，再解析 AST，只递归解释数字、括号和 ``+ - * /``，同时
限制文本长度、AST 节点、递归深度、运算次数、字面量和结果绝对值。失败返回稳定的人类
可读错误，不向模型暴露堆栈。当前工具集是能力演示；GitHub/Jira 等有副作用操作仍通过
显式业务客户端控制，未来 MCP 工具也必须补充权限、审批、审计、超时与幂等策略。
"""

import ast
import math
import operator
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

_EXPRESSION_RE = re.compile(r"^[0-9+\-*/().\s]+$")
_MAX_EXPRESSION_LENGTH = 128
_MAX_AST_NODES = 64
_MAX_AST_DEPTH = 12
_MAX_OPERATIONS = 20
_MAX_LITERAL_MAGNITUDE = 1_000_000_000_000
_MAX_RESULT_MAGNITUDE = 1_000_000_000_000_000
_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class UnsafeExpression(ValueError):
    """内部安全拒绝信号：表达式语法不受支持或可能造成资源耗尽。

    对外 ``calculate`` 会把它转换为通用错误消息，避免泄露解释器细节；内部函数用同一种
    异常统一表达类型、深度、次数、数值范围和除零等拒绝原因。
    """


def _bounded_number(value: int | float, *, literal: bool = False) -> int | float:
    """校验数字类型、有限性和幅度，并原样返回安全值。

    ``bool`` 虽是 Python ``int`` 子类仍被明确拒绝；字面量使用更严格上限，运算中间结果
    使用结果上限，避免构造极大整数消耗 CPU/内存。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnsafeExpression
    if isinstance(value, float) and not math.isfinite(value):
        raise UnsafeExpression
    limit = _MAX_LITERAL_MAGNITUDE if literal else _MAX_RESULT_MAGNITUDE
    if abs(value) > limit:
        raise UnsafeExpression
    return value


def _evaluate_expression(node: ast.AST, operations: list[int], depth: int = 0) -> int | float:
    """递归解释白名单 AST，并共享累计运算计数。

    ``operations`` 使用单元素列表是为了让递归分支共享可变计数器，不是业务输出。节点类型
    未显式允许就 fail-closed，因此幂运算、函数调用、名称、下标和属性访问都会被拒绝。
    """
    if depth > _MAX_AST_DEPTH:
        raise UnsafeExpression
    if isinstance(node, ast.Expression):
        return _evaluate_expression(node.body, operations, depth + 1)
    if isinstance(node, ast.Constant):
        return _bounded_number(node.value, literal=True)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        operations[0] += 1
        if operations[0] > _MAX_OPERATIONS:
            raise UnsafeExpression
        operand = _evaluate_expression(node.operand, operations, depth + 1)
        return _bounded_number(_UNARY_OPERATORS[type(node.op)](operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        operations[0] += 1
        if operations[0] > _MAX_OPERATIONS:
            raise UnsafeExpression
        left = _evaluate_expression(node.left, operations, depth + 1)
        right = _evaluate_expression(node.right, operations, depth + 1)
        if isinstance(node.op, ast.Div) and right == 0:
            raise UnsafeExpression
        return _bounded_number(_BINARY_OPERATORS[type(node.op)](left, right))
    raise UnsafeExpression


@tool
def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """获取指定时区的当前时间。

    Args:
        timezone: IANA 时区名，例如 "Asia/Shanghai"、"UTC"。

    Returns:
        包含实际采用时区和秒级本地时间的中文文本。

    非法或运行时不可用的时区会确定性回退到上海时区，避免工具异常中断整个 ReAct 循环。
    本函数读取系统时钟，因此结果不可用于需要可信时间戳/签名的审计场景。
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

    Returns:
        成功时返回 ``原表达式 = 结果``；字符非法、语法错误、除零、复杂度或数值越界时
        返回稳定错误文本，不抛出可恢复的用户输入异常。
    """
    if (
        not expression
        or len(expression) > _MAX_EXPRESSION_LENGTH
        or not _EXPRESSION_RE.fullmatch(expression)
    ):
        return "表达式包含不支持的字符，仅允许数字与 + - * / ( ) "
    try:
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _node in ast.walk(tree)) > _MAX_AST_NODES:
            raise UnsafeExpression
        result = _evaluate_expression(tree, [0])
        return f"{expression} = {result}"
    except (SyntaxError, UnsafeExpression, ZeroDivisionError, OverflowError):
        return "计算失败：表达式无效、过于复杂或结果超出范围"


def get_builtin_tools() -> list:
    """返回绑定给通用 ReAct Agent 的固定工具列表。

    返回新列表但元素是模块级 ``StructuredTool`` 对象；调用方可绑定，不应原地修改工具
    元数据。当前列表刻意不包含任何网络写操作。
    """
    return [get_current_time, calculate]
