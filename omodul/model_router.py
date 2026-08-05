"""omodul.model_router — cost-aware multi-model dynamic routing.

3O layer: omodul (transaction orchestration).
Routes tasks onto compute tiers by task metadata + content complexity
(oprim.task_tier), invoking host-injected flagship/economy LLM callers, and
books every call into obase.cost_tracker so the host can audit spend.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from obase.cost_tracker import CostTracker
from oprim._task_tier import TIER_FLAGSHIP, classify_tier, complexity_score

_log = logging.getLogger(__name__)


class ModelRouter:
    """Dynamic model router: cheap work -> economy tier, hard reasoning -> flagship."""

    def __init__(
        self,
        *,
        flagship_caller: Callable,
        economy_caller: Callable,
        flagship_model: str = "flagship",
        economy_model: str = "economy",
        cost_tracker: CostTracker | None = None,
    ):
        """
        Args:
            flagship_caller: Host-injected LLM function for high-compute tier
                (async (messages, **kwargs) -> OpenAI-format dict).
            economy_caller: Host-injected LLM function for cheap tier.
            flagship_model / economy_model: Labels for reporting/audit.
            cost_tracker: obase CostTracker (shared across the host).
        """
        self.flagship_caller = flagship_caller
        self.economy_caller = economy_caller
        self.flagship_model = flagship_model
        self.economy_model = economy_model
        self.cost_tracker = cost_tracker or CostTracker()
        # 路由统计(算力经济学审计)
        self._stats = {"FLAGSHIP": {"calls": 0, "tokens": 0}, "ECONOMY": {"calls": 0, "tokens": 0}}

    async def completion(
        self,
        prompt: str,
        task_type: str = "general",
        system_prompt: str | None = None,
        *,
        complexity_hint: float | None = None,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Route + complete: returns {tier_used, model, content, usage}."""
        # 1. 动态意图路由判定(任务元数据 + 内容复杂度)
        hint = complexity_hint if complexity_hint is not None else complexity_score(prompt)
        tier = classify_tier(task_type, complexity_hint=hint)

        caller = self.flagship_caller if tier == TIER_FLAGSHIP else self.economy_caller
        model = self.flagship_model if tier == TIER_FLAGSHIP else self.economy_model

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # 2. 调用选定的模型
        response = await caller(messages, temperature=temperature)
        content = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        usage = response.get("usage") or {}

        # 3. 成本记账(obase CostTracker, 失败不影响路由) + 路由统计
        in_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0
        out_tokens = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0
        try:
            if in_tokens:
                self.cost_tracker.record(
                    category="llm", provider="router",
                    model_or_tier=model, unit="token_in", quantity=in_tokens,
                )
            if out_tokens:
                self.cost_tracker.record(
                    category="llm", provider="router",
                    model_or_tier=model, unit="token_out", quantity=out_tokens,
                )
        except Exception:  # noqa: BLE001 — pricing table gaps must not break routing
            _log.warning("model_router: cost booking skipped (pricing table gap)")
        self._stats[tier]["calls"] += 1
        self._stats[tier]["tokens"] += in_tokens + out_tokens

        _log.info("model_router: %s tier -> %s (task_type=%s)", tier, model, task_type)
        return {
            "tier_used": tier,
            "model": model,
            "content": content,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
        }

    def get_stats(self) -> dict[str, Any]:
        """Tier call/token ledger (cost economics audit)."""
        try:
            cost_usd = float(self.cost_tracker.total_usd)
        except Exception:  # noqa: BLE001 — cost ledger gaps must not break stats
            cost_usd = 0.0
        return {
            "tiers": self._stats,
            "total_calls": sum(s["calls"] for s in self._stats.values()),
            "cost_usd": cost_usd,
        }
