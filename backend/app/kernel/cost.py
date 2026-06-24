"""成本治理：通过 LiteLLM 回调累计各模型 token 用量与费用。

提供进程内聚合（P0 简化版）。生产应落库 / 接 Langfuse，并支持按租户、按 Agent 维度。
"""

import threading
from dataclasses import dataclass, field

import litellm

from app.core.logging import get_logger

logger = get_logger("cost")


@dataclass
class ModelUsage:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class CostTracker:
    """线程安全的成本聚合器。"""

    total_requests: int = 0
    total_cost_usd: float = 0.0
    by_model: dict[str, ModelUsage] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, model: str, prompt: int, completion: int, cost: float) -> None:
        with self._lock:
            self.total_requests += 1
            self.total_cost_usd += cost
            mu = self.by_model.setdefault(model, ModelUsage())
            mu.requests += 1
            mu.prompt_tokens += prompt
            mu.completion_tokens += completion
            mu.cost_usd += cost

    def snapshot(self) -> dict:
        with self._lock:
            return {
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

    def reset(self) -> None:
        with self._lock:
            self.total_requests = 0
            self.total_cost_usd = 0.0
            self.by_model.clear()


tracker = CostTracker()


def _success_callback(kwargs, completion_response, start_time, end_time) -> None:  # noqa: ANN001
    """LiteLLM 成功回调：每次 LLM 调用后累计用量与费用。"""
    try:
        model = kwargs.get("model", "unknown")
        usage = getattr(completion_response, "usage", None)
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        try:
            cost = litellm.completion_cost(completion_response=completion_response) or 0.0
        except Exception:  # noqa: BLE001 - 国内模型常无定价，费用记 0 但仍统计 token
            cost = 0.0
        tracker.record(model, prompt, completion, float(cost))
    except Exception as exc:  # noqa: BLE001 - 成本统计绝不能影响主流程
        logger.warning("cost_callback_failed", error=str(exc))


def install_cost_tracking() -> None:
    """注册 LiteLLM 成功回调（应用启动时调用一次）。"""
    if _success_callback not in litellm.success_callback:
        litellm.success_callback.append(_success_callback)
        logger.info("cost_tracking_installed")
