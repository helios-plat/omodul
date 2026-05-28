"""fusion_score_workflow — 13-dimension ADR-037 three-layer fusion score computation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint


class FusionScoreConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "fusion_score_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}
    _fingerprint_fields: ClassVar[set[str]] = {"symbol", "snapshot_ts", "pack_id"}

    symbol: str
    snapshot_ts: str  # ISO datetime
    pack_id: str = "default"


def compute_fingerprint_for(config: FusionScoreConfig, input_data: dict) -> str:
    """Compute deterministic fingerprint for a fusion score run.

    Args:
        config: Fusion score configuration.
        input_data: Input data dict (dimension scores, weights, etc.).

    Returns:
        SHA-256 hex string (64 chars).
    """
    return compute_fingerprint(config, input_data)


def fusion_score_workflow(
    config: FusionScoreConfig,
    input_data: dict,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """Compute 13-dimension fusion score with ADR-037 three-layer model.

    Orchestrates Layer0 (redline veto), Layer1 (core multiplicative),
    Layer2 (adjustment additive) to produce final fusion score.

    Args:
        config: FusionScoreConfig with symbol, snapshot_ts, pack_id.
        input_data: Dict with keys: dimensions (list), weights (dict), redlines (list).
        output_dir: Directory for report output.
        on_step: Optional callback for step-by-step progress.

    Returns:
        Dict with findings, status, fingerprint, decision_trail, report_path.
    """
    from obase.cost_tracker import CostTracker

    started_at = datetime.now(UTC)
    trail_steps: list[dict] = []
    cost_tracker = CostTracker()
    fingerprint = compute_fingerprint_for(config, input_data)

    try:
        # Step 1: Layer0 — Redline check
        step_start = datetime.now(UTC)
        redlines = input_data.get("redlines", [])
        breached = [r for r in redlines if r.get("triggered")]
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oprim",
            callable_name="redline_check",
            inputs_summary={"redline_count": len(redlines)},
            outputs_summary={"breached": len(breached)},
            started_at=step_start,
        )

        # Step 2: Layer1 — Core multiplicative scoring
        step_start = datetime.now(UTC)
        dimensions = input_data.get("dimensions", [])
        weights = input_data.get("weights", {})
        long_sum = sum(
            d.get("value", 0) * weights.get(d["name"], 0)
            for d in dimensions
            if d.get("side") == "long"
        )
        short_sum = sum(
            abs(d.get("value", 0)) * weights.get(d["name"], 0)
            for d in dimensions
            if d.get("side") == "short"
        )
        core = {
            "long": long_sum,
            "short": short_sum,
            "direction": "bullish" if long_sum > short_sum else "bearish",
        }
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="core_layer_compute",
            inputs_summary={"dim_count": len(dimensions)},
            outputs_summary=core,
            started_at=step_start,
        )

        # Step 3: Layer2 — Adjustments
        step_start = datetime.now(UTC)
        adjustments = input_data.get("adjustments", {})
        adj_total = sum(adjustments.values()) if adjustments else 0
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oprim",
            callable_name="adjustment_layer",
            inputs_summary={"adjustment_keys": list(adjustments.keys())},
            outputs_summary={"total": adj_total},
            started_at=step_start,
        )

        # Final score
        raw = long_sum - short_sum + adj_total
        final_score = max(-100, min(100, int(raw)))
        if breached:
            final_score = 0

        findings = {
            "finalScore": final_score,
            "finalTier": "bullish"
            if final_score > 20
            else "bearish"
            if final_score < -20
            else "neutral",
            "core": core,
            "adjustments": {"total": adj_total},
            "redlines": redlines,
            "dimensions": dimensions,
        }

        # Report
        report_path = None
        if "report" in config._enabled_pillars:
            report_path = output_dir / f"fusion_{config.symbol}_{config.snapshot_ts}.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                f"# Fusion Score: {config.symbol}\n\n"
                f"Score: {final_score}\nTier: {findings['finalTier']}\n"
            )

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
            "report_path": report_path,
            "cost_usd": cost_tracker.total_usd,
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
            "cost_usd": cost_tracker.total_usd,
        }
