from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from obase.cost_tracker import CostTracker
from oprim.crypto.hashing import sha256_hash
from oprim.serialization.canonical import canonical_json


def record_step(
    *,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
    layer: Literal["oskill", "oprim", "oprim_batch", "external"],
    callable_name: str,
    inputs_summary: dict[str, Any],
    outputs_summary: dict[str, Any],
    started_at: datetime,
    status: Literal["completed", "failed", "partial"] = "completed",
    error: dict[str, Any] | None = None,
    llm_call_file: str | None = None,
) -> None:
    """记录一个 step 到 trail_steps + 调用 on_step 回调 (服务层 SSE 用)."""
    step = {
        "step_no": len(trail_steps) + 1,
        "started_at_utc": started_at.isoformat(),
        "elapsed_ms": int((datetime.now(UTC) - started_at).total_seconds() * 1000),
        "layer": layer,
        "callable": callable_name,
        "inputs_hash": sha256_hash(canonical_json(inputs_summary)),
        "inputs_summary": inputs_summary,
        "outputs_summary": outputs_summary,
        "outputs_hash": sha256_hash(canonical_json(outputs_summary)),
        "status": status,
        "error": error,
    }
    if llm_call_file:
        step["llm_call_file"] = llm_call_file

    trail_steps.append(step)
    if on_step:
        try:
            on_step(step)
        except Exception:
            pass


def build_decision_trail(
    *,
    fingerprint: str,
    config: Any,
    input_data: Any,
    trail_steps: list[dict[str, Any]],
    cost_tracker: CostTracker,
    started_at: datetime,
    status: str,
    error: dict[str, Any] | None,
) -> dict[str, Any]:
    """组装 decision_trail 完整 dict."""
    from omodul._fingerprint import _hash_input_data

    return {
        "fingerprint": fingerprint,
        "omodul_name": getattr(config, "_omodul_name", ""),
        "omodul_version": getattr(config, "_omodul_version", ""),
        "started_at_utc": started_at.isoformat(),
        "ended_at_utc": datetime.now(UTC).isoformat(),
        "elapsed_seconds": (datetime.now(UTC) - started_at).total_seconds(),
        "config_snapshot": config.model_dump() if hasattr(config, "model_dump") else str(config),
        "input_fingerprint": _hash_input_data(input_data, strategy="pydantic_canonical"),
        "status": status,
        "steps": trail_steps,
        "cost_breakdown": {
            "llm_cost_usd": cost_tracker.total_usd,
            "total_cost_usd": cost_tracker.total_usd,
        },
        "error": error,
    }
