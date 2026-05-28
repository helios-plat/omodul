"""omodul.regime_inference — 市场 regime 推断.

IO 剥离: 服务层预取 today_indicators + raw_history 注入.
组合: multi_state_classify(oskill) → regime_smoothing(oskill).
Pillars: fingerprint + decision_trail (no cost, no report).
"""

from __future__ import annotations

import hashlib
import traceback
from datetime import UTC, datetime, date
from pathlib import Path
from typing import Any, ClassVar, Set

from obase.cost_tracker import CostTracker
from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from pydantic import BaseModel, field_validator

_VERSION = "1.0.0"

_DEFAULT_STATE_DEFS = [
    {
        "name": "extreme_hot",
        "conditions": [{"field": "limit_up_count", "op": "gte", "value": 80}],
        "priority": 1,
    },
    {
        "name": "hot",
        "conditions": [{"field": "limit_up_count", "op": "gte", "value": 40}],
        "priority": 2,
    },
    {
        "name": "warm",
        "conditions": [{"field": "limit_up_count", "op": "gte", "value": 20}],
        "priority": 3,
    },
    {
        "name": "neutral",
        "conditions": [{"field": "limit_up_count", "op": "lt", "value": 20}],
        "priority": 4,
    },
    {
        "name": "cold",
        "conditions": [{"field": "broken_rate", "op": "gte", "value": 0.4}],
        "priority": 5,
    },
    {
        "name": "extreme_cold",
        "conditions": [{"field": "broken_rate", "op": "gte", "value": 0.6}],
        "priority": 6,
    },
]

_DEFAULT_SMOOTHING_CFG = {
    "stress_states": ["extreme_hot", "extreme_cold"],
    "stress_min_days": 2,
    "normal_min_days": 3,
}


def compute_fingerprint_for(config: "RegimeInferenceConfig", input_data: Any) -> str:
    """公开 fingerprint API. 依赖 {trade_date, smoothing_window}."""
    raw = f"{config.trade_date.isoformat()}|{config.smoothing_window}|{_VERSION}"
    return hashlib.sha256(raw.encode()).hexdigest()


class RegimeInferenceConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "regime_inference"
    _omodul_version: ClassVar[str] = _VERSION
    _enabled_pillars: ClassVar[Set[str]] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[Set[str]] = {"trade_date", "smoothing_window"}

    trade_date: date
    smoothing_window: int = 5
    state_definitions: list[dict[str, Any]] | None = None

    @field_validator("smoothing_window")
    @classmethod
    def _check_window(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"smoothing_window must be >= 1, got {v}")
        return v


class RegimeInferenceInput(BaseModel):
    today_indicators: dict[str, float]
    raw_history: list[dict[str, Any]]
    current_smoothed_state: str | None = None


def regime_inference(
    config: RegimeInferenceConfig,
    input_data: RegimeInferenceInput,
    output_dir: Path | None = None,
    *,
    on_step: "Callable[[dict[str, Any]], None] | None" = None,
) -> dict[str, Any]:
    """Market regime 推断. 纯同步, IO-free.

    Returns:
        dict: regime, raw_regime, confidence, state_changed, persistence_days,
              transitional_state, fingerprint, decision_trail, status, error.
    """
    from collections.abc import Callable
    from oskill.regime.multi_state_classify import multi_state_classify
    from oskill.regime_smoothing import regime_smoothing
    from oskill.types import RawRegimeState, SmoothingConfig

    started_at = datetime.now(UTC)
    fingerprint = compute_fingerprint_for(config, input_data)
    cost_tracker = CostTracker()
    trail_steps: list[dict[str, Any]] = []
    status = "completed"
    error_info = None
    result_data: dict[str, Any] = {
        "regime": "unknown", "raw_regime": "unknown", "confidence": 0.0,
        "state_changed": False, "persistence_days": 0, "transitional_state": None,
    }

    try:
        state_defs = config.state_definitions or _DEFAULT_STATE_DEFS

        # Step 1: raw classify
        step_start = datetime.now(UTC)
        classify_result = multi_state_classify(
            indicators=input_data.today_indicators,
            state_definitions=state_defs,
        )
        raw_state = classify_result["current_state"]
        confidence = float(classify_result["confidence"])
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="multi_state_classify",
            inputs_summary={"indicator_keys": list(input_data.today_indicators.keys())},
            outputs_summary={"raw_state": raw_state, "confidence": confidence},
            started_at=step_start,
        )

        # Step 2: build history + smoothing
        step_start = datetime.now(UTC)
        smoothing_config = SmoothingConfig(**_DEFAULT_SMOOTHING_CFG)
        raw_history: list[RawRegimeState] = []
        for entry in input_data.raw_history[-(config.smoothing_window) :]:
            raw_history.append(
                RawRegimeState(
                    date=datetime.fromisoformat(str(entry["date"])).replace(tzinfo=UTC)
                    if not isinstance(entry["date"], datetime)
                    else entry["date"],
                    state=str(entry["state"]),
                    confidence=float(entry.get("confidence", 1.0)),
                )
            )
        raw_history.append(
            RawRegimeState(
                date=datetime.combine(config.trade_date, datetime.min.time()).replace(tzinfo=UTC),
                state=raw_state,
                confidence=confidence,
            )
        )
        smooth_result = regime_smoothing(
            raw_state_history=raw_history,
            smoothing_config=smoothing_config,
            current_smoothed_state=input_data.current_smoothed_state,
        )
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="regime_smoothing",
            inputs_summary={
                "history_len": len(raw_history),
                "current": input_data.current_smoothed_state,
            },
            outputs_summary={
                "smoothed_state": smooth_result.smoothed_state,
                "state_changed": smooth_result.state_changed,
            },
            started_at=step_start,
        )

        result_data = {
            "regime": smooth_result.smoothed_state,
            "raw_regime": raw_state,
            "confidence": confidence,
            "state_changed": smooth_result.state_changed,
            "persistence_days": smooth_result.days_in_current_state,
            "transitional_state": smooth_result.transitional_state,
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
