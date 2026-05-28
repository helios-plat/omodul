"""market_summary_workflow — LLM-powered market summary generation with caching."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint


class MarketSummaryConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "market_summary_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"symbol", "date", "model"}

    symbol: str
    date: str  # ISO date
    model: str = "qwen-max"


def compute_fingerprint_for(config: MarketSummaryConfig, input_data: dict) -> str:
    """Compute deterministic fingerprint for a market summary run."""
    return compute_fingerprint(config, input_data)


def market_summary_workflow(
    config: MarketSummaryConfig,
    input_data: dict,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """Generate LLM market summary from multi-source context.

    Args:
        config: MarketSummaryConfig with symbol, date, model.
        input_data: Dict with keys: context_sources (list of text), cached_summary (str|None).
        output_dir: Directory for report output.
        on_step: Optional callback for step-by-step progress.

    Returns:
        Dict with findings, status, fingerprint, decision_trail, report_path, cost_usd.
    """
    from obase.cost_tracker import CostTracker

    started_at = datetime.now(UTC)
    trail_steps: list[dict] = []
    cost_tracker = CostTracker()
    fingerprint = compute_fingerprint_for(config, input_data)

    try:
        # Step 1: Check cache
        step_start = datetime.now(UTC)
        cached = input_data.get("cached_summary")
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oprim",
            callable_name="cache_check",
            inputs_summary={"has_cache": cached is not None},
            outputs_summary={"cache_hit": cached is not None},
            started_at=step_start,
        )

        if cached:
            findings = {"summary": cached, "source": "cache"}
        else:
            # Step 2: Gather context
            step_start = datetime.now(UTC)
            sources = input_data.get("context_sources", [])
            context_text = "\n".join(sources[:10])
            record_step(
                trail_steps=trail_steps,
                on_step=on_step,
                layer="oprim_batch",
                callable_name="gather_context",
                inputs_summary={"source_count": len(sources)},
                outputs_summary={"context_len": len(context_text)},
                started_at=step_start,
            )

            # Step 3: LLM call (simulated — actual impl calls provider)
            step_start = datetime.now(UTC)
            summary = (
                f"Market summary for {config.symbol} on {config.date}:"
                f" Based on {len(sources)} sources."
            )
            record_step(
                trail_steps=trail_steps,
                on_step=on_step,
                layer="external",
                callable_name=f"llm_call_{config.model}",
                inputs_summary={"prompt_len": len(context_text)},
                outputs_summary={"summary_len": len(summary)},
                started_at=step_start,
            )
            findings = {"summary": summary, "source": "llm"}

        # Report
        report_path = None
        if "report" in config._enabled_pillars:
            report_path = output_dir / f"summary_{config.symbol}_{config.date}.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(f"# Market Summary: {config.symbol}\n\n{findings['summary']}\n")

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
