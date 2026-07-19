"""Multi-engine consensus workflow — the ensemble "brain". 4-pillar omodul.

Orchestrates, per instrument:
    1. oskill.signal.sentiment_onchain_synthesis — FGI + on-chain -> biases.
    2. oskill.consensus.engine_consensus — fuse promoted engine signals with
       decay / weights / regime / sentiment into an executable consensus.

Extraction source: helixa services/prob-engine. The load-bearing difference:
only engines whose own gate passed (`promoted`) drive the executable consensus;
un-promoted engines are recorded but contribute 0 (helixa let everything vote).
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


class ConsensusConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "consensus_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}
    _fingerprint_fields: ClassVar[set[str]] = {"instrument", "base_threshold"}

    instrument: str = "BTC-USDT-SWAP"
    base_threshold: float = 0.45
    max_kelly: float = 0.20
    divergence_threshold: float = 0.55
    ttl_seconds: float = 3600.0


def consensus_workflow(
    config: ConsensusConfig,
    input_data: dict,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """Fuse one instrument's engine signals into a consensus.

    input_data keys: ``signals`` (list[dict] per-engine), ``weights`` (dict),
    ``regime_state`` (str), ``fgi`` (float|None), ``onchain`` (dict|None with
    flow_in/flow_out/mvrv), ``news_sentiment`` (float|None, [0,1] keyword score
    from iris md.sentiment metric=news_sentiment — a minor nudge on sentiment_bias,
    see oskill.signal.sentiment_onchain_synthesis.news_sentiment_nudge).
    """
    from obase.cost_tracker import CostTracker

    started_at = datetime.now(UTC)
    trail_steps: list[dict] = []
    cost_tracker = CostTracker()
    fingerprint = compute_fingerprint(config, {"instrument": config.instrument})

    try:
        from oskill.consensus.engine_consensus import engine_consensus
        from oskill.signal.sentiment_onchain_synthesis import (
            fgi_sentiment_bias,
            news_sentiment_nudge,
            onchain_signal,
            risk_on_off_signal,
        )

        t0 = datetime.now(UTC)
        fgi = input_data.get("fgi")
        sentiment_bias = fgi_sentiment_bias(float(fgi))["bias"] if fgi is not None else 0.0
        news_score = input_data.get("news_sentiment")
        if news_score is not None:
            sentiment_bias = max(
                -1.0, min(1.0, sentiment_bias + news_sentiment_nudge(float(news_score)))
            )
        tf = input_data.get("tradfi")
        macro_bias = (
            risk_on_off_signal(
                equity_returns=tf.get("equity_returns", []),
                dxy_returns=tf.get("dxy_returns", []),
            )["bias"]
            if tf
            else 0.0
        )
        oc = input_data.get("onchain")
        onchain_bias = (
            onchain_signal(
                flow_in=oc.get("flow_in", 0.0),
                flow_out=oc.get("flow_out", 0.0),
                mvrv=oc.get("mvrv", 1.0),
            )["signal"]
            if oc
            else 0.0
        )
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="sentiment_onchain_synthesis",
            inputs_summary={"fgi": fgi, "has_onchain": oc is not None},
            outputs_summary={
                "sentiment_bias": round(sentiment_bias, 3),
                "onchain_bias": round(onchain_bias, 3),
            },
            started_at=t0,
        )

        t0 = datetime.now(UTC)
        cons = engine_consensus(
            input_data["signals"],
            weights=input_data.get("weights", {}),
            regime_state=input_data.get("regime_state", "range"),
            sentiment_bias=sentiment_bias,
            onchain_bias=onchain_bias,
            macro_bias=macro_bias,
            ttl_seconds=config.ttl_seconds,
            base_threshold=config.base_threshold,
            max_kelly=config.max_kelly,
            divergence_threshold=config.divergence_threshold,
        )
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="engine_consensus",
            inputs_summary={"n_signals": len(input_data["signals"])},
            outputs_summary={
                "direction": cons["final_direction"],
                "score": round(cons["consensus_score"], 3),
                "execute": cons["should_execute"],
            },
            started_at=t0,
        )

        findings = {**cons, "sentiment_bias": sentiment_bias, "onchain_bias": onchain_bias}

        report_path = None
        if "report" in config._enabled_pillars:
            report_path = output_dir / f"{config._omodul_name}_{fingerprint[:8]}.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                f"# consensus_workflow ({config.instrument})\n\n{json.dumps(findings, default=str, indent=2)}\n"
            )
        trail = build_decision_trail(
            fingerprint=fingerprint,
            config=config,
            input_data={"instrument": config.instrument},
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
            input_data={"instrument": config.instrument},
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
