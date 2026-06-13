"""omodul.funding_arb — Funding-rate arbitrage opportunity scanner.

Pillars: cost, decision_trail
Composites: oprim.funding_rate_fetch + oskill.market_impact_sigmoid
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from omodul._base import BaseConfig, Trail, build_result


class FundingArbConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "funding_arb"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"cost", "decision_trail"}
    _fingerprint_fields: ClassVar[set[str]] = {"symbol", "notional"}

    symbol: str
    notional: float = 100_000.0
    adv: float = 10_000_000.0
    funding_limit: int = 50
    venue: str = "okx"
    impact_params: dict[str, Any] = {"alpha": 30.0, "beta": 5.0, "gamma": 0.05}


def funding_arb(
    *,
    config: FundingArbConfig,
    funding_rates: list[dict[str, Any]] | None = None,
    config_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan funding-rate arbitrage opportunities accounting for market impact.

    Composites:
        1. oprim.funding_rate_fetch  — historical funding rate data.
        2. oskill.market_impact_sigmoid — sigmoid impact model (no sqrt law).

    Args:
        config: FundingArbConfig.
        funding_rates: Pre-fetched list (skips network when provided).
        config_override: Passed to funding_rate_fetch (e.g. custom base URL).

    Returns:
        Result with ``mean_funding_rate``, ``impact_bps``, ``net_bps``,
        ``arb_viable``, ``rates_sampled``.
    """
    from oskill.market_impact_sigmoid import market_impact_sigmoid  # noqa: PLC0415

    trail = Trail()

    if funding_rates is None:
        from oprim.funding_rate_fetch import funding_rate_fetch  # noqa: PLC0415

        funding_rates = asyncio.run(
            funding_rate_fetch(
                config.symbol,
                venue=config.venue,
                limit=config.funding_limit,
                config=config_override,
            )
        )

    trail.record(event="rates_fetched", count=len(funding_rates))

    mean_rate = (
        sum(r["funding_rate"] for r in funding_rates) / len(funding_rates)
        if funding_rates else 0.0
    )
    annual_funding_bps = mean_rate * 3 * 365 * 10_000

    impact = market_impact_sigmoid(
        config.notional, adv=config.adv, params=config.impact_params
    )
    impact_bps = impact["impact_bps"]
    round_trip_bps = impact_bps * 2
    net_bps = annual_funding_bps - round_trip_bps
    arb_viable = net_bps > 0

    trail.record(event="impact_computed", impact_bps=impact_bps, net_bps=net_bps)

    return build_result(
        status="ok",
        trail=trail,
        cost_usd=0.0,
        mean_funding_rate=mean_rate,
        annual_funding_bps=annual_funding_bps,
        impact_bps=impact_bps,
        net_bps=net_bps,
        arb_viable=arb_viable,
        rates_sampled=len(funding_rates),
    )
