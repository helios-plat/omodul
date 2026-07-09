"""Market-regime classification workflow — 4-pillar omodul.

Wraps oskill.regime.market_regime_deterministic (primary) or
oskill.regime.market_regime_hmm (optional, when hmmlearn is available) to
produce a crisis/trend/range regime label with a decision trail.

Extraction source: helixa services/regime-detector. helivex prefers the
deterministic classifier because its own research found HMM market regimes have
no out-of-sample persistence (commit 646dc71, 11/11 FAIL); the regime output is
therefore treated by consumers as an ADVISORY soft input, never a hard gate.
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


class RegimeConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "regime_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}
    _fingerprint_fields: ClassVar[set[str]] = {"method", "symbol"}

    method: str = "deterministic"  # "deterministic" | "hmm"
    symbol: str = "BTC-USDT-SWAP"
    vol_window: int = 60
    autocorr_window: int = 30
    momentum_window: int = 120


def regime_workflow(
    config: RegimeConfig,
    input_data: dict,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """Classify the current regime from ``input_data['closes']``.

    ``method="hmm"`` falls back to the deterministic classifier (and records the
    fallback in the decision trail) if hmmlearn is unavailable — the workflow
    never fails just because the optional HMM dependency is missing.
    """
    from obase.cost_tracker import CostTracker

    started_at = datetime.now(UTC)
    trail_steps: list[dict] = []
    cost_tracker = CostTracker()
    fingerprint = compute_fingerprint(config, {"symbol": config.symbol})

    try:
        closes = input_data["closes"]
        method_used = config.method
        step_start = datetime.now(UTC)

        if config.method == "hmm":
            try:
                from oskill.regime.market_regime_hmm import market_regime_hmm

                result = market_regime_hmm(
                    closes,
                    vol_window=config.vol_window,
                    autocorr_window=config.autocorr_window,
                    momentum_window=config.momentum_window,
                )
            except Exception:  # hmmlearn missing or fit failure → deterministic
                from oskill.regime.market_regime_deterministic import (
                    market_regime_deterministic,
                )

                method_used = "deterministic_fallback"
                result = market_regime_deterministic(
                    closes,
                    vol_window=config.vol_window,
                    autocorr_window=config.autocorr_window,
                    momentum_window=config.momentum_window,
                )
        else:
            from oskill.regime.market_regime_deterministic import (
                market_regime_deterministic,
            )

            result = market_regime_deterministic(
                closes,
                vol_window=config.vol_window,
                autocorr_window=config.autocorr_window,
                momentum_window=config.momentum_window,
            )

        findings = {
            "state": result["state"],
            "confidence": result["confidence"],
            "method_used": method_used,
            "rows_used": result["rows_used"],
            "detail": {
                k: v for k, v in result.items() if k not in ("state", "confidence", "rows_used")
            },
            "advisory": True,  # regime is a soft input, never a hard gate (646dc71)
        }
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name=f"market_regime_{method_used}",
            inputs_summary={"symbol": config.symbol, "n_closes": len(closes)},
            outputs_summary={"state": findings["state"], "confidence": findings["confidence"]},
            started_at=step_start,
        )

        report_path = None
        if "report" in config._enabled_pillars:
            report_path = output_dir / f"{config._omodul_name}_{fingerprint[:8]}.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                f"# regime_workflow ({config.symbol})\n\n{json.dumps(findings, default=str, indent=2)}\n"
            )

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
