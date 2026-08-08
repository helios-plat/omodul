"""CVaR portfolio-risk workflow — 4-pillar omodul (fingerprint/decision_trail/report/cost).

Orchestrates, per cycle:
    1. oskill.portfolio.cvar_optimal_weights   — CVaR-Sharpe portfolio weights
    2. oskill.risk.position_size_tiers          — per-symbol 3-tier position cap
    3. omodul.risk_models.drawdown_circuit_breaker (optional, if equity_curve given)

Follows the same generic-runner pattern as omodul.helios_workflows._run_workflow;
extraction source: helixa project, services/portfolio-optimizer +
services/risk-engine (see oprim.risk.cvar_portfolio_optimize / atr_position_cap /
net_exposure_clip docstrings for the exact production formulas ported).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pandas as pd
from pydantic import ConfigDict

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint


class CvarRiskConfig(BaseConfig):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    _omodul_name: ClassVar[str] = "cvar_risk_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {
        "fingerprint",
        "decision_trail",
        "report",
        "cost",
    }
    _fingerprint_fields: ClassVar[set[str]] = {"symbols", "lookback_days", "alpha"}

    symbols: list[str] = []
    lookback_days: int = 30
    alpha: float = 0.05
    min_obs: int = 50
    atr_risk_budget: float = 0.01
    atr_min_position: float = 0.005
    atr_max_position: float = 0.20
    correlation_pairs: dict[str, dict[str, float]] = {}
    max_net_exposure: float = 0.25
    min_trade_notional: float = 10.0
    slippage_scale: float = 1.0
    max_asset_weight: float = 0.5
    min_asset_weight: float = 0.15


def _run_workflow(
    config: CvarRiskConfig,
    input_data: dict,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
    stages: list[tuple[str, str, Callable[[dict], dict]]],
) -> dict:
    """Generic omodul workflow runner with 4-pillar support (mirrors
    omodul.helios_workflows._run_workflow — not shared cross-module by
    convention in this package, each workflow file owns its own copy)."""
    from obase.cost_tracker import CostTracker

    started_at = datetime.now(UTC)
    trail_steps: list[dict] = []
    cost_tracker = CostTracker()
    fingerprint = compute_fingerprint(config, input_data)

    try:
        findings: dict = {}
        for layer, name, fn in stages:
            step_start = datetime.now(UTC)
            result = fn(input_data)
            findings.update(result)
            input_data = {**input_data, **result}
            record_step(
                trail_steps=trail_steps,
                on_step=on_step,
                layer=layer,
                callable_name=name,
                inputs_summary={"keys": list(input_data.keys())[:5]},
                outputs_summary={"keys": list(result.keys())[:5]},
                started_at=step_start,
            )

        report_path = None
        if "report" in getattr(config, "_enabled_pillars", set()):
            report_path = output_dir / f"{config._omodul_name}_{fingerprint[:8]}.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                f"# {config._omodul_name}\n\n{json.dumps(findings, default=str, indent=2)}\n"
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


def cvar_risk_workflow(
    config: CvarRiskConfig,
    input_data: dict,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """CVaR portfolio weights + 3-tier position caps for one adapter cycle.

    Parameters
    ----------
    config : CvarRiskConfig
    input_data : dict
        Required keys: ``returns`` (pd.DataFrame, columns=symbols),
        ``current_positions_usd`` (dict[symbol, float]), ``capital_usd`` (float),
        ``atr_pct`` (dict[symbol, float]).
        Optional: ``equity_curve`` (list[float]) — if present, also runs the
        drawdown circuit breaker and includes its status in `findings`.
    output_dir : Path

    Returns
    -------
    dict
        Standard omodul return shape (see module docstring / `_run_workflow`).
        ``findings`` contains ``weights`` (from cvar_optimal_weights) and
        ``position_caps`` (per-symbol dict from position_size_tiers), plus
        ``circuit_breaker`` if `equity_curve` was supplied.
    """

    def _stage_weights(d: dict) -> dict:
        from oskill.portfolio.cvar_optimal_weights import cvar_optimal_weights

        returns: pd.DataFrame = d["returns"]
        result = cvar_optimal_weights(
            returns,
            alpha=config.alpha,
            min_obs=config.min_obs,
            max_weight=config.max_asset_weight,
            min_weight=config.min_asset_weight,
        )
        return {"weights": result}

    def _stage_sizing(d: dict) -> dict:
        from oskill.risk.position_size_tiers import position_size_tiers

        weights = d["weights"]["weights"]
        capital_usd = d["capital_usd"]
        current_positions = d.get("current_positions_usd", {})
        atr_pct_map = d.get("atr_pct", {})
        symbols = list(weights.keys())

        caps: dict[str, dict] = {}
        for sym in symbols:
            pairs = config.correlation_pairs.get(sym, {})
            # position_size_tiers' correlated_positions contract is fraction-of-capital
            # (see its docstring), not raw USD — current_positions is USD notional, so
            # it must be divided by capital_usd here. Passing raw USD made the tier-3
            # net-exposure check compare a USD number against a 0..1 fraction bound,
            # clipping any correlated instrument's cap to ~0 whenever the other side
            # of the pair held any position at all.
            correlated_positions = [
                (
                    (current_positions.get(other, 0.0) / capital_usd) if capital_usd > 0 else 0.0,
                    corr,
                )
                for other, corr in pairs.items()
            ]
            caps[sym] = position_size_tiers(
                proposed_notional=weights[sym] * capital_usd,
                optimal_weight=weights[sym],
                capital_usd=capital_usd,
                slippage_scale=config.slippage_scale,
                current_position_usd=current_positions.get(sym, 0.0),
                atr_pct=atr_pct_map.get(sym, 0.0) or 1e-9,
                atr_risk_budget=config.atr_risk_budget,
                atr_min_position=config.atr_min_position,
                atr_max_position=config.atr_max_position,
                correlated_positions=correlated_positions,
                max_net_exposure=config.max_net_exposure,
                min_trade_notional=config.min_trade_notional,
            )
        return {"position_caps": caps}

    def _stage_circuit_breaker(d: dict) -> dict:
        if "equity_curve" not in d or d["equity_curve"] is None:
            return {"circuit_breaker": None}
        from omodul.risk_models import drawdown_circuit_breaker

        cb = drawdown_circuit_breaker(
            equity_curve=d["equity_curve"],
            daily_loss_halt_pct=d.get("daily_loss_halt_pct", 0.03),
            weekly_loss_halt_pct=d.get("weekly_loss_halt_pct", 0.10),
            max_position_notional_usd=d["capital_usd"],
            max_total_notional_usd=d["capital_usd"],
            volatility_halt_multiplier=d.get("volatility_halt_multiplier", 2.0),
            halt_recovery_hours=d.get("halt_recovery_hours", 24),
            recent_realized_vol=d.get("recent_realized_vol", 1.0),
            baseline_realized_vol=d.get("baseline_realized_vol", 1.0),
        )
        return {"circuit_breaker": cb}

    return _run_workflow(
        config,
        input_data,
        output_dir,
        on_step=on_step,
        stages=[
            ("oskill", "cvar_optimal_weights", _stage_weights),
            ("oskill", "position_size_tiers", _stage_sizing),
            ("omodul", "drawdown_circuit_breaker", _stage_circuit_breaker),
        ],
    )
