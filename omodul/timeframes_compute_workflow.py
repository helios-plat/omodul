"""timeframes_compute_workflow — Multi-timeframe technical analysis computation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint


class TimeframesConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "timeframes_compute_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[set[str]] = {"symbol", "snapshot_ts"}

    symbol: str
    snapshot_ts: str  # ISO datetime


def compute_fingerprint_for(config: TimeframesConfig, input_data: dict) -> str:
    """Compute deterministic fingerprint for a timeframes computation."""
    return compute_fingerprint(config, input_data)


def timeframes_compute_workflow(
    config: TimeframesConfig,
    input_data: dict,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """Compute multi-timeframe technical analysis (TF1 strategic + TF2 trend + TF3 entry).

    Args:
        config: TimeframesConfig with symbol, snapshot_ts.
        input_data: Dict with keys: klines (dict of timeframe→bars), fgi (int|None).
        output_dir: Directory for output (unused — no report pillar).
        on_step: Optional callback for step-by-step progress.

    Returns:
        Dict with findings (tf1/tf2/tf3), status, fingerprint, decision_trail.
    """
    from obase.cost_tracker import CostTracker

    started_at = datetime.now(UTC)
    trail_steps: list[dict] = []
    cost_tracker = CostTracker()
    fingerprint = compute_fingerprint_for(config, input_data)

    try:
        klines = input_data.get("klines", {})

        # Step 1: Compute per-frame indicators
        step_start = datetime.now(UTC)
        frames = {}
        for tf, bars in klines.items():
            if not bars:
                continue
            closes = [b.get("close", 0) for b in bars]
            current = closes[-1] if closes else 0
            frames[tf] = {
                "current_price": current,
                "trend": "up" if len(closes) > 1 and closes[-1] > closes[0] else "down",
                "bar_count": len(bars),
            }
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oprim_batch",
            callable_name="compute_frame_indicators",
            inputs_summary={"timeframes": list(klines.keys())},
            outputs_summary={"frames_computed": len(frames)},
            started_at=step_start,
        )

        # Step 2: TF1 strategic state
        step_start = datetime.now(UTC)
        fgi = input_data.get("fgi")
        tf1 = {
            "strategic": {
                "state": "bullish"
                if fgi and fgi > 50
                else "bearish"
                if fgi and fgi < 30
                else "neutral",
                "confidence": 0.7 if fgi else 0.3,
            },
            "frames": {k: v for k, v in frames.items() if k in ("1M", "1w")},
        }
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="tf1_strategic",
            inputs_summary={"fgi": fgi},
            outputs_summary={"state": tf1["strategic"]["state"]},
            started_at=step_start,
        )

        # Step 3: TF2 trend alignment
        step_start = datetime.now(UTC)
        daily = frames.get("1d", {})
        h4 = frames.get("4h", {})
        tf2 = {
            "trend": {
                "daily_direction": daily.get("trend", "neutral"),
                "h4_direction": h4.get("trend", "neutral"),
                "alignment": "aligned" if daily.get("trend") == h4.get("trend") else "divergent",
            },
            "frames": {k: v for k, v in frames.items() if k in ("1d", "4h")},
        }
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="tf2_trend",
            inputs_summary={},
            outputs_summary=tf2["trend"],
            started_at=step_start,
        )

        # Step 4: TF3 entry
        step_start = datetime.now(UTC)
        tf3 = {
            "entry": {"rating": "neutral"},
            "frames": {k: v for k, v in frames.items() if k in ("1h", "15m")},
        }
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="tf3_entry",
            inputs_summary={},
            outputs_summary=tf3["entry"],
            started_at=step_start,
        )

        findings = {"tf1": tf1, "tf2": tf2, "tf3": tf3}

        trail = build_decision_trail(
            fingerprint=fingerprint,
            config=config,
            input_data=input_data,
            trail_steps=trail_steps,
            cost_tracker=cost_tracker,
            started_at=started_at,
            status="completed",
            error=None,
        )

        return {
            "findings": findings,
            "status": "completed",
            "error": None,
            "fingerprint": fingerprint,
            "decision_trail": trail,
            "report_path": None,
            "cost_usd": 0.0,
        }

    except Exception as exc:
        trail = build_decision_trail(
            fingerprint=fingerprint,
            config=config,
            input_data=input_data,
            trail_steps=trail_steps,
            cost_tracker=cost_tracker,
            started_at=started_at,
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        return {
            "findings": None,
            "status": "failed",
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "fingerprint": fingerprint,
            "decision_trail": trail,
            "report_path": None,
            "cost_usd": 0.0,
        }
