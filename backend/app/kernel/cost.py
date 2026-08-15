"""LLM 成本治理：从 LiteLLM 成功回调聚合 token 与估算费用。

架构位置：应用启动时 ``install_cost_tracking`` 向 LiteLLM 注册回调；所有经 LLM 网关发出的
成功请求都会进入 ``tracker``。管理 API 通过 ``snapshot_for_backend`` 查询统一快照，并可
通过受保护入口重置。local/test 默认使用线程安全内存统计，非本地默认用 Redis Hash 原子
聚合，使 API、worker 与多副本看到同一份数据。

典型调用示例::

    install_cost_tracking()  # lifespan 启动阶段，仅调用一次
    model = get_chat_model()
    await model.ainvoke(messages)  # 成功后由回调自动记账
    snapshot = await tracker.snapshot_for_backend()

为什么 best-effort：计费可观测性不能让已完成的模型主请求因 Redis/价格表故障被判失败。
Redis 写失败时记录到当前进程的内存降级缓冲，管理快照会标记 ``degraded``；该缓冲无法在
副本间合并或跨重启恢复，因此生产监控必须对降级告警，不能把本模块结果当供应商账单。
费用来自 LiteLLM 响应或价格表，是工程估算而非结算凭据。当前不做按用户/租户预算拦截、
账期存档与财务对账，这些属于未来治理能力。
"""

import math
import threading
from dataclasses import dataclass, field
from typing import Any

import litellm

from app.core.config import get_settings
from app.core.logging import get_logger
from app.kernel.queue import get_queue

logger = get_logger("cost")


@dataclass
class ModelUsage:
    """单个模型在一个聚合后端中的累加器。

    字段保存原始累计值，只有生成快照时才对美元费用四舍五入，避免每次请求舍入导致长期
    漂移。此类由 ``CostTracker`` 的锁保护，不应在锁外直接修改。
    """

    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class CostTracker:
    """线程安全的内存聚合器，以及内存/Redis 的统一异步门面。

    LiteLLM 同步回调可能来自不同线程，因此内存字段由 ``threading.Lock`` 保护；Redis 路径
    使用服务共享的异步连接池和事务 pipeline。类实例 ``tracker`` 是进程单例，但没有依赖
    隐式后台线程。
    """

    total_requests: int = 0
    total_cost_usd: float = 0.0
    by_model: dict[str, ModelUsage] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, model: str, prompt: int, completion: int, cost: float) -> None:
        """规范化并原子累加当前进程统计。

        可用于配置的 memory 后端，也作为 Redis 写失败的降级缓冲。输入可能来自第三方
        回调，负 token、NaN/负费用等会在加锁前归一化，避免污染共享状态。
        """
        model, prompt, completion, cost = _normalize_usage(model, prompt, completion, cost)
        with self._lock:
            self.total_requests += 1
            self.total_cost_usd += cost
            mu = self.by_model.setdefault(model, ModelUsage())
            mu.requests += 1
            mu.prompt_tokens += prompt
            mu.completion_tokens += completion
            mu.cost_usd += cost

    def snapshot(self) -> dict:
        """在同一把锁下复制一致的当前进程快照。

        返回普通 dict，调用方修改它不会影响内部计数；``total_tokens`` 是展示时由 prompt
        与 completion 相加得到的派生值。
        """
        with self._lock:
            result = {
                "total_requests": self.total_requests,
                "total_cost_usd": round(self.total_cost_usd, 6),
                "by_model": {
                    model: {
                        "requests": u.requests,
                        "prompt_tokens": u.prompt_tokens,
                        "completion_tokens": u.completion_tokens,
                        "total_tokens": u.prompt_tokens + u.completion_tokens,
                        "cost_usd": round(u.cost_usd, 6),
                    }
                    for model, u in self.by_model.items()
                },
            }
        return result

    def reset(self) -> None:
        """原子清空当前进程统计，主要供管理接口和测试使用。"""
        with self._lock:
            self.total_requests = 0
            self.total_cost_usd = 0.0
            self.by_model.clear()

    async def record_for_backend(
        self,
        model: str,
        prompt: int,
        completion: int,
        cost: float,
    ) -> None:
        """按配置写入 disabled、memory 或 Redis 后端。

        Redis pipeline 使用单次事务原子增加总计与分模型四个字段，避免管理 API 读到只写
        了一半的请求。异常会降级写内存并记录异常类型，绝不向 LLM 回调抛出。
        """
        settings = get_settings()
        backend = settings.resolved_cost_tracking_backend
        if backend == "disabled":
            return
        if backend == "memory":
            self.record(model, prompt, completion, cost)
            return

        model, prompt, completion, cost = _normalize_usage(model, prompt, completion, cost)
        key = settings.cost_redis_key
        try:
            redis = await get_queue()
            # 同一请求的总计与分模型指标必须一起成功；否则二者无法对账。
            pipe = redis.pipeline(transaction=True)
            pipe.hincrby(key, "total:requests", 1)
            pipe.hincrbyfloat(key, "total:cost_usd", cost)
            prefix = f"model:{model}:"
            pipe.hincrby(key, prefix + "requests", 1)
            pipe.hincrby(key, prefix + "prompt_tokens", prompt)
            pipe.hincrby(key, prefix + "completion_tokens", completion)
            pipe.hincrbyfloat(key, prefix + "cost_usd", cost)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - 成本统计不可影响模型请求
            self.record(model, prompt, completion, cost)
            logger.warning("cost_redis_record_failed", error_type=type(exc).__name__)

    async def snapshot_for_backend(self) -> dict:
        """读取当前配置后端，并附加 ``storage``/``degraded`` 元数据。

        Redis 不可用时返回本进程降级缓冲，而不是空数据；调用方必须展示降级标志，因为
        多副本其他进程的成本此时不可见。
        """
        settings = get_settings()
        backend = settings.resolved_cost_tracking_backend
        if backend == "disabled":
            return {
                "total_requests": 0,
                "total_cost_usd": 0.0,
                "by_model": {},
                "storage": "disabled",
                "degraded": False,
            }
        if backend == "memory":
            return {**self.snapshot(), "storage": "memory", "degraded": False}

        try:
            redis = await get_queue()
            raw = await redis.hgetall(settings.cost_redis_key)
            return {
                **_snapshot_from_redis(raw),
                "storage": "redis",
                "degraded": False,
            }
        except Exception as exc:  # noqa: BLE001 - 管理面返回明确降级快照
            logger.warning("cost_redis_snapshot_failed", error_type=type(exc).__name__)
            return {
                **self.snapshot(),
                "storage": "memory-fallback",
                "degraded": True,
            }

    async def reset_for_backend(self) -> None:
        """重置共享 Redis 统计（如启用）及本地降级缓冲。

        Redis 删除失败会向上传播，避免管理端误报“已清空”；memory/disabled 路径只清理
        当前进程。生产调用应受管理员鉴权和审计保护。
        """
        settings = get_settings()
        backend = settings.resolved_cost_tracking_backend
        if backend == "redis":
            redis = await get_queue()
            await redis.delete(settings.cost_redis_key)
        self.reset()


tracker = CostTracker()


def _normalize_usage(
    model: str,
    prompt: int,
    completion: int,
    cost: float,
) -> tuple[str, int, int, float]:
    """把第三方回调字段规范化成安全、可聚合的固定类型。

    模型名裁剪为 200 字符，避免攻击者控制的动态模型名制造超长 Redis field；token 下限
    为零，费用只接受非负有限浮点数。异常的整数/浮点转换仍会向调用它的回调层抛出，并由
    外层 best-effort 捕获。
    """
    safe_model = str(model or "unknown")[:200]
    safe_prompt = max(0, int(prompt or 0))
    safe_completion = max(0, int(completion or 0))
    safe_cost = float(cost or 0.0)
    if not math.isfinite(safe_cost) or safe_cost < 0:
        safe_cost = 0.0
    return safe_model, safe_prompt, safe_completion, safe_cost


def _usage_from_callback(kwargs: Any, completion_response: Any) -> tuple[str, int, int, float]:
    """从不同 LiteLLM 版本的回调载荷提取模型、token 与费用。

    ``usage`` 兼容 dict 和属性对象。供应商未返回 ``response_cost`` 时尝试 LiteLLM 价格表；
    国内/私有模型没有价格条目时费用降级为 0，但 token 仍保留。该值不是财务账单。
    """
    model = kwargs.get("model", "unknown") if isinstance(kwargs, dict) else "unknown"
    usage = getattr(completion_response, "usage", None)
    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens", 0) or 0
        completion = usage.get("completion_tokens", 0) or 0
    else:
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0

    cost = kwargs.get("response_cost") if isinstance(kwargs, dict) else None
    if cost is None:
        try:
            cost = litellm.completion_cost(completion_response=completion_response) or 0.0
        except Exception:  # noqa: BLE001 - 国内模型可能不在价格表中
            cost = 0.0
    return _normalize_usage(model, prompt, completion, cost)


def _decode_redis(value: Any) -> str:
    """兼容 Redis 客户端返回 bytes 或已解码标量。"""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _snapshot_from_redis(raw: dict[Any, Any]) -> dict:
    """把扁平 Redis Hash 安全还原为管理 API 的分模型结构。

    Redis field 采用 ``model:{模型名}:{指标}``。用 ``rsplit`` 而不是普通 split，允许模型名
    自身包含冒号。未知字段或无法解析的值被忽略/置零并记录字段名，不让单个脏字段使整个
    管理接口不可用。
    """
    decoded = {_decode_redis(key): _decode_redis(value) for key, value in raw.items()}
    by_model: dict[str, dict[str, int | float]] = {}
    for field_name, raw_value in decoded.items():
        if not field_name.startswith("model:"):
            continue
        model_and_metric = field_name.removeprefix("model:")
        if ":" not in model_and_metric:
            continue
        model, metric = model_and_metric.rsplit(":", 1)
        if metric not in {"requests", "prompt_tokens", "completion_tokens", "cost_usd"}:
            continue
        usage = by_model.setdefault(
            model,
            {
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
            },
        )
        try:
            usage[metric] = float(raw_value) if metric == "cost_usd" else int(raw_value)
        except ValueError:
            logger.warning("cost_redis_value_invalid", field=field_name)

    rendered_models = {}
    for model, usage in by_model.items():
        prompt = int(usage["prompt_tokens"])
        completion = int(usage["completion_tokens"])
        rendered_models[model] = {
            **usage,
            "total_tokens": prompt + completion,
            "cost_usd": round(float(usage["cost_usd"]), 6),
        }
    try:
        total_requests = int(decoded.get("total:requests", "0"))
        total_cost = float(decoded.get("total:cost_usd", "0"))
    except ValueError:
        logger.warning("cost_redis_totals_invalid")
        total_requests, total_cost = 0, 0.0
    return {
        "total_requests": total_requests,
        "total_cost_usd": round(total_cost, 6),
        "by_model": rendered_models,
    }


def _success_callback(kwargs, completion_response, start_time, end_time) -> None:  # noqa: ANN001
    """LiteLLM 同步成功回调：写 memory 后端并吞掉所有统计异常。

    参数签名由 LiteLLM 约定，时间字段当前不参与聚合；保留它们是为了兼容回调协议。
    """
    try:
        tracker.record(*_usage_from_callback(kwargs, completion_response))
    except Exception as exc:  # noqa: BLE001 - 成本统计绝不能影响主流程
        logger.warning("cost_callback_failed", error_type=type(exc).__name__)


async def _async_success_callback(  # noqa: ANN001
    kwargs, completion_response, start_time, end_time
) -> None:
    """LiteLLM 异步成功回调：通过统一门面写 Redis/降级内存。"""
    try:
        await tracker.record_for_backend(*_usage_from_callback(kwargs, completion_response))
    except Exception as exc:  # noqa: BLE001 - 成本统计绝不能影响主流程
        logger.warning("cost_callback_failed", error_type=type(exc).__name__)


def install_cost_tracking() -> None:
    """按配置幂等注册对应的 LiteLLM 成功回调。

    disabled 不注册；memory 使用公开同步列表；Redis 当前使用 LiteLLM 的异步回调列表。
    ``not in`` 检查防止测试重复启动或开发热重载在同一进程中重复计费。异步列表属于第三方
    兼容点，升级 LiteLLM 时应由测试确认；未来宜迁移到其稳定公开回调 API。
    """
    backend = get_settings().resolved_cost_tracking_backend
    if backend == "disabled":
        logger.info("cost_tracking_disabled")
        return
    if backend == "memory" and _success_callback not in litellm.success_callback:
        litellm.success_callback.append(_success_callback)
    elif backend == "redis" and _async_success_callback not in litellm._async_success_callback:
        litellm._async_success_callback.append(_async_success_callback)
    logger.info("cost_tracking_installed", backend=backend)
