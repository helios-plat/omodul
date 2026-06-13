"""omodul.stat_arb — Statistical arbitrage signal with impact-adjusted sizing.

Pillars: cost, decision_trail
Composites: oskill.cointegration_pairs + oskill.market_impact_sigmoid
"""
from __future__ import annotations

from typing import Any, ClassVar

from omodul._base import BaseConfig, Trail, build_result


class StatArbConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "stat_arb"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"cost", "decision_trail"}
    _fingerprint_fields: ClassVar[set[str]] = {"symbol_a", "symbol_b", "notional"}

    symbol_a: str
    symbol_b: str
    notional: float = 100_000.0
    adv_a: float = 5_000_000.0
    adv_b: float = 5_000_000.0
    entry_z: float = 2.0
    exit_z: float = 0.5
    lookback: int = 60
    impact_params: dict[str, Any] = {"alpha": 30.0, "beta": 5.0, "gamma": 0.05}


def stat_arb(
    series_a: Any,
    series_b: Any,
    *,
    config: StatArbConfig,
) -> dict[str, Any]:
    """Generate a stat-arb signal and estimate net-of-impact edge.

    Composites:
        1. oskill.cointegration_pairs  — cointegration test + z-score signal.
        2. oskill.market_impact_sigmoid — round-trip impact for both legs.

    Args:
        series_a: Price history for instrument A, length T.
        series_b: Price history for instrument B, length T.
        config: StatArbConfig.

    Returns:
        Result with ``signal``, ``zscore``, ``cointegrated``,
        ``impact_bps_a``, ``impact_bps_b``, ``total_impact_bps``, ``arb_viable``.
    """
    from oskill.cointegration_pairs import cointegration_pairs  # noqa: PLC0415
    from oskill.market_impact_sigmoid import market_impact_sigmoid  # noqa: PLC0415

    trail = Trail()

    pairs_result = cointegration_pairs(
        series_a, series_b,
        entry_z=config.entry_z,
        exit_z=config.exit_z,
        lookback=config.lookback,
    )
    trail.record(event="pairs_signal",
                 signal=pairs_result["signal"],
                 zscore=pairs_result["zscore"],
                 cointegrated=pairs_result["cointegrated"])

    impact_a = market_impact_sigmoid(
        config.notional, adv=config.adv_a, params=config.impact_params
    )
    impact_b = market_impact_sigmoid(
        config.notional * abs(pairs_result["hedge_ratio"]),
        adv=config.adv_b, params=config.impact_params,
    )
    total_impact_bps = (impact_a["impact_bps"] + impact_b["impact_bps"]) * 2

    series_b_list = list(series_b)
    mean_price = float(sum(series_b_list) / len(series_b_list)) if series_b_list else 1.0
    spread_std = pairs_result["zscore_result"].get("std", 1.0) or 1.0
    expected_edge_bps = (
        abs(pairs_result["zscore"]) * spread_std / max(mean_price, 1e-8) * 10_000
    )

    arb_viable = (
        pairs_result["signal"] in ("long_a_short_b", "short_a_long_b")
        and expected_edge_bps > total_impact_bps
    )
    trail.record(event="impact_computed",
                 total_impact_bps=total_impact_bps,
                 expected_edge_bps=expected_edge_bps)

    return build_result(
        status="ok",
        trail=trail,
        cost_usd=0.0,
        signal=pairs_result["signal"],
        zscore=pairs_result["zscore"],
        cointegrated=pairs_result["cointegrated"],
        hedge_ratio=pairs_result["hedge_ratio"],
        impact_bps_a=impact_a["impact_bps"],
        impact_bps_b=impact_b["impact_bps"],
        total_impact_bps=total_impact_bps,
        expected_edge_bps=expected_edge_bps,
        arb_viable=arb_viable,
    )
