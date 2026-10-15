"""LLM-persona directional signal engine — SLOT (disabled by default, no cost).

4-pillar omodul. Mirrors helixa's ai-hedge-fund / finrobot / tradingagents
engines (LLM personas voting a direction from market context). Built as a
capability slot so helivex can be a full superset of helixa, but:

  - **Disabled by default** (`config.enabled=False`) — returns a neutral,
    zero-cost result WITHOUT any LLM API call, so the roster is complete but
    nothing is billed unless deliberately turned on.
  - **Gated like every other engine** — even when enabled its output is
    observe-only until it clears the same promotion discipline; helixa itself
    ran these at weight 0.0 in production ("token cost too high, no signal
    quality advantage"), so promotion should require real attribution evidence.

When enabled, `input_data` must carry `context` (a compact market-state string)
and the omodul would call the configured provider; that call path is
intentionally left as a single clearly-marked hook rather than wired to a live
key here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint


class LlmSignalConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "llm_signal_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"symbol", "persona"}

    symbol: str = "BTC-USDT-SWAP"
    persona: str = "macro_analyst"
    enabled: bool = False  # cost guard — no API call unless deliberately enabled


def llm_signal_workflow(
    config: LlmSignalConfig,
    input_data: dict,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """Return an LLM-persona directional vote. Disabled by default (neutral,
    zero cost, no API call). ``promoted`` is always False from this slot until
    an attribution-backed enablement path is wired."""
    from obase.cost_tracker import CostTracker

    started_at = datetime.now(UTC)
    trail_steps: list[dict] = []
    cost_tracker = CostTracker()
    fingerprint = compute_fingerprint(config, {"symbol": config.symbol})

    try:
        if not config.enabled:
            findings = {
                "direction": "neutral",
                "score": 0.0,
                "confidence": 0.0,
                "promoted": False,
                "enabled": False,
                "note": "LLM engine slot disabled (no API cost). Enable via config.enabled + wire provider.",
            }
        else:
            # Deliberate enablement path — provider call hook. Left unwired so a
            # missing/misconfigured key can never silently bill; enabling this is
            # an explicit, reviewed change (see module docstring).
            raise NotImplementedError(
                "LLM provider call hook is intentionally unwired; enabling requires "
                "an explicit provider + key wiring and attribution-backed review."
            )

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="omodul",
            callable_name="llm_persona_vote",
            inputs_summary={"symbol": config.symbol, "persona": config.persona},
            outputs_summary={"direction": findings["direction"], "enabled": findings["enabled"]},
            started_at=started_at,
        )
        report_path = None
        trail = build_decision_trail(
            fingerprint=fingerprint,
            config=config,
            input_data={"symbol": config.symbol},
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
            input_data={"symbol": config.symbol},
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
