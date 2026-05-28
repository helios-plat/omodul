"""omodul.candidate_pool — 候选池构建.

IO 剥离: 服务层预取 universe + dim_scores + screen_rules 注入.
H1 合规: 不调用 symbol_dim_score omodul; dim_scores 由服务层预算后经 input_data 传入.
组合: apply_screen_filter(oprim) → regime_conditional_score_weighted(oskill).
Pillars: fingerprint + decision_trail (no cost, no report).
"""

from __future__ import annotations

import hashlib
import traceback
from collections.abc import Callable
from datetime import UTC, datetime, date
from pathlib import Path
from typing import Any, ClassVar, Set

from obase.cost_tracker import CostTracker
from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from oprim.apply_screen_filter import ScreenRule, apply_screen_filter
from pydantic import BaseModel, Field, field_validator

_VERSION = "1.0.0"

_BASE_WEIGHTS: dict[str, float] = {
    "technical": 0.20,
    "fundamentals": 0.15,
    "valuation": 0.15,
    "sentiment": 0.10,
    "risk": 0.15,
    "liquidity": 0.10,
    "policy": 0.05,
    "pattern": 0.10,
}

_REGIME_OVERRIDES: dict[str, dict[str, float]] = {
    "hot": {"technical": 1.5, "momentum": 1.3, "risk": 0.7},
    "cold": {"valuation": 1.5, "risk": 1.3, "technical": 0.7},
    "neutral": {},
}


def compute_fingerprint_for(config: "CandidatePoolConfig", input_data: Any) -> str:
    """公开 fingerprint API. 依赖 {regime, scope, trade_date}."""
    raw = f"{config.regime}|{config.scope}|{config.trade_date.isoformat()}|{_VERSION}"
    return hashlib.sha256(raw.encode()).hexdigest()


class CandidatePoolConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "candidate_pool"
    _omodul_version: ClassVar[str] = _VERSION
    _enabled_pillars: ClassVar[Set[str]] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[Set[str]] = {"regime", "scope", "trade_date"}

    regime: str
    scope: str = "A_share"
    trade_date: date
    top_n: int = 20

    @field_validator("top_n")
    @classmethod
    def _check_top_n(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"top_n must be >= 1, got {v}")
        return v


class CandidatePoolInput(BaseModel):
    universe: list[dict[str, Any]]
    dim_scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    market_distribution: dict[str, Any] = Field(default_factory=dict)
    screen_rules: list[dict[str, Any]] = Field(default_factory=list)


def candidate_pool(
    config: CandidatePoolConfig,
    input_data: CandidatePoolInput,
    output_dir: Path | None = None,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """候选池构建. 纯同步, IO-free, H1-compliant.

    Returns:
        dict: candidates, n_total, n_after_filter, regime,
              fingerprint, decision_trail, status, error.
    """
    import pandas as pd
    from oskill.regime_conditional_score_weighted import regime_conditional_score_weighted

    started_at = datetime.now(UTC)
    fingerprint = compute_fingerprint_for(config, input_data)
    cost_tracker = CostTracker()
    trail_steps: list[dict[str, Any]] = []
    status = "completed"
    error_info = None
    result_data: dict[str, Any] = {}

    try:
        universe = input_data.universe
        n_total = len(universe)

        if not universe:
            result_data = {
                "candidates": [],
                "n_total": 0,
                "n_after_filter": 0,
                "regime": config.regime,
            }
        else:
            df = pd.DataFrame(universe)
            if "symbol" not in df.columns:
                raise ValueError("universe entries must contain 'symbol' field")

            # Step 1: apply_screen_filter
            step_start = datetime.now(UTC)
            rules = [ScreenRule(**r) for r in input_data.screen_rules]
            screen_result = apply_screen_filter(candidates=df, rules=rules)
            passed_symbols = set(screen_result.passed)
            n_after_filter = len(passed_symbols)
            record_step(
                trail_steps=trail_steps,
                on_step=on_step,
                layer="oprim",
                callable_name="apply_screen_filter",
                inputs_summary={"n_universe": n_total, "n_rules": len(rules)},
                outputs_summary={
                    "n_passed": n_after_filter,
                    "n_rejected": n_total - n_after_filter,
                },
                started_at=step_start,
            )

            # Step 2: regime_conditional_score_weighted
            step_start = datetime.now(UTC)
            regime_overrides = _REGIME_OVERRIDES.get(config.regime, {})
            scored: list[tuple[str, float]] = []
            for symbol in passed_symbols:
                dim_scores = input_data.dim_scores.get(symbol)
                if not dim_scores:
                    scored.append((symbol, 50.0))
                    continue
                common_dims = set(dim_scores.keys()) & set(_BASE_WEIGHTS.keys())
                if not common_dims:
                    scored.append((symbol, 50.0))
                    continue
                sub_scores = {k: dim_scores[k] for k in common_dims}
                sub_weights = {k: _BASE_WEIGHTS[k] for k in common_dims}
                total_w = sum(sub_weights.values())
                sub_weights = {k: v / total_w for k, v in sub_weights.items()}
                try:
                    weighted = regime_conditional_score_weighted(
                        dim_scores=sub_scores,
                        base_weights=sub_weights,
                        regime_weight_overrides={config.regime: regime_overrides},
                        current_regime=config.regime,
                    )
                    scored.append((symbol, weighted.total_score))
                except Exception:
                    scored.append((symbol, 50.0))

            record_step(
                trail_steps=trail_steps,
                on_step=on_step,
                layer="oskill",
                callable_name="regime_conditional_score_weighted",
                inputs_summary={"n_scored": len(scored), "regime": config.regime},
                outputs_summary={"top_score": max((s for _, s in scored), default=0.0)},
                started_at=step_start,
            )

            scored.sort(key=lambda x: -x[1])
            top_candidates = scored[: config.top_n]
            result_data = {
                "candidates": top_candidates,
                "n_total": n_total,
                "n_after_filter": n_after_filter,
                "regime": config.regime,
            }

    except Exception as exc:
        status = "failed"
        error_info = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }

    trail = build_decision_trail(
        fingerprint=fingerprint,
        config=config,
        input_data=input_data,
        trail_steps=trail_steps,
        cost_tracker=cost_tracker,
        started_at=started_at,
        status=status,
        error=error_info,
    )

    return {
        **result_data,
        "fingerprint": fingerprint,
        "decision_trail": trail,
        "status": status,
        "error": error_info,
    }
