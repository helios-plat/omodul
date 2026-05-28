"""cross_asset_opportunity_ranking omodul."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint


class CrossAssetRankingConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "cross_asset_opportunity_ranking"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "report"}
    _fingerprint_fields: ClassVar[set[str]] = {"asset_classes", "date"}
    asset_classes: list[str] = ["crypto", "us_equity", "commodity"]
    date: str = ""


def cross_asset_opportunity_ranking(
    config: CrossAssetRankingConfig,
    input_data: dict,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """Rank cross-asset opportunities by normalized relative strength.

    Calls oskill.cross_asset_score_normalization to normalize heterogeneous
    fusion scores, then generates actionable ranking with insights.
    """
    from obase.cost_tracker import CostTracker

    started_at = datetime.now(UTC)
    trail_steps: list[dict] = []
    cost_tracker = CostTracker()
    fingerprint = compute_fingerprint(config, input_data)

    try:
        scores = input_data.get("fusion_scores", {})
        histories = input_data.get("fusion_histories", {})

        # Stage 1: Normalize
        step_start = datetime.now(UTC)
        from oskill.wave2_skills import cross_asset_score_normalization
        norm_result = cross_asset_score_normalization(asset_scores=scores, asset_histories=histories)
        record_step(trail_steps=trail_steps, on_step=on_step, layer="oskill", callable_name="cross_asset_score_normalization", inputs_summary={"assets": list(scores.keys())}, outputs_summary={"top": norm_result["top_opportunity"]}, started_at=step_start)

        # Stage 2: Generate insight
        step_start = datetime.now(UTC)
        ranking = norm_result["ranking"]
        assets_data = norm_result["assets"]
        if len(assets_data) >= 2:
            gap = (assets_data[0].get("normalized_score") or 0) - (assets_data[1].get("normalized_score") or 0)
            insight = f"{ranking[0]} leads by {gap:.1f} points over {ranking[1]}." if gap > 2 else "No clear cross-asset opportunity today."
        else:
            gap = 0
            insight = "Insufficient assets for comparison."
        record_step(trail_steps=trail_steps, on_step=on_step, layer="oprim", callable_name="insight_generation", inputs_summary={}, outputs_summary={"insight": insight[:50]}, started_at=step_start)

        findings = {"normalized_ranking": assets_data, "top_pick": norm_result["top_opportunity"], "opportunity_gap": round(gap, 2), "actionable_insight": insight}

        # Report
        report_path = None
        if "report" in config._enabled_pillars:
            report_path = output_dir / f"cross_asset_ranking_{config.date}.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            lines = [f"# Cross-Asset Opportunity Ranking — {config.date}\n", f"**Top Pick**: {findings['top_pick']}\n", f"**Insight**: {insight}\n", "\n## Ranking\n"]
            for a in assets_data:
                lines.append(f"- {a['asset']}: raw={a['raw_score']}, norm={a.get('normalized_score')}, pct={a.get('percentile_rank')}\n")
            report_path.write_text("".join(lines))

        trail = build_decision_trail(fingerprint=fingerprint, config=config, input_data=input_data, trail_steps=trail_steps, cost_tracker=cost_tracker, started_at=started_at, status="completed", error=None)
        return {"findings": findings, "status": "completed", "error": None, "fingerprint": fingerprint, "decision_trail": trail, "report_path": report_path, "cost_usd": 0.0}

    except Exception as exc:
        trail = build_decision_trail(fingerprint=fingerprint, config=config, input_data=input_data, trail_steps=trail_steps, cost_tracker=cost_tracker, started_at=started_at, status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        return {"findings": None, "status": "failed", "error": {"type": type(exc).__name__, "message": str(exc)}, "fingerprint": fingerprint, "decision_trail": trail, "report_path": None, "cost_usd": 0.0}
