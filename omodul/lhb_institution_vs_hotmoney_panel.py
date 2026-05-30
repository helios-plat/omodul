"""omodul.lhb_institution_vs_hotmoney_panel — 龙虎榜机构 vs 游资面板.

Pillars: fingerprint + decision_trail
H1 compliant: calls oskills only (no sibling omodul).
Composition (B10 oskills + oprim):
  - oskill.seat_winrate_aggregator      (sync)
  - oskill.unknown_seats_audit_loop     (sync)
  - oprim.fetch_sector_returns          (async → asyncio.run)
"""

from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar, Set

from obase.cost_tracker import CostTracker
from pydantic import BaseModel, Field

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint


class LhbPanelConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "lhb_institution_vs_hotmoney_panel"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[Set[str]] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[Set[str]] = {"trade_date", "symbol_scope"}

    trade_date: date
    symbol_scope: str = "all"
    match_threshold: float = 0.80
    high_risk_percentile: float = 75.0


class LhbPanelInput(BaseModel):
    seat_trades: list[dict[str, Any]] = Field(
        default_factory=list,
        description="SeatTradeInput-compatible dicts: seat_name, net_buy_yi, buy_date, sell_date",
    )
    observed_seats: list[str] = Field(default_factory=list)
    net_buys: list[float] = Field(default_factory=list)
    known_tycoon_seats: list[str] = Field(default_factory=list)


class LhbPanelFindings(BaseModel):
    trade_date: str
    symbol_scope: str
    n_seats_analyzed: int = 0
    top_seats_by_winrate: list[dict[str, Any]] = Field(default_factory=list)
    high_risk_unknown_count: int = 0
    top_sector_returns: list[dict[str, Any]] = Field(default_factory=list)
    audit_summary: dict[str, Any] = Field(default_factory=dict)


def compute_fingerprint_for(
    config: LhbPanelConfig,
    input_data: LhbPanelInput,
) -> str:
    return compute_fingerprint(config, input_data)


def lhb_institution_vs_hotmoney_panel(
    config: LhbPanelConfig,
    input_data: LhbPanelInput,
    output_dir: Path | None = None,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Build LHB institution vs hot-money panel.

    Returns dict with: findings, fingerprint, decision_trail, status, error.
    """
    from oskill.seat_winrate_aggregator import SeatTradeInput, seat_winrate_aggregator
    from oskill.unknown_seats_audit_loop import unknown_seats_audit_loop
    import oprim

    started_at = datetime.now(UTC)
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    cost_tracker = CostTracker()
    trail_steps: list[dict[str, Any]] = []
    fingerprint = compute_fingerprint_for(config, input_data)

    findings: LhbPanelFindings | None = None
    error: dict[str, Any] | None = None
    status = "completed"
    trail: dict[str, Any] = {}

    try:
        # Step 1: seat winrate aggregation
        t0 = datetime.now(UTC)
        seat_inputs: list[SeatTradeInput] = []
        for st in input_data.seat_trades:
            try:
                seat_inputs.append(SeatTradeInput(**st))
            except Exception:
                pass

        if seat_inputs:
            winrate_report = seat_winrate_aggregator(seat_trades=seat_inputs)
            top_seats = [r.model_dump() for r in winrate_report.seats[:10]]
            n_seats = len(winrate_report.seats)
        else:
            top_seats = []
            n_seats = 0

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="seat_winrate_aggregator",
            inputs_summary={"n_seat_trades": len(seat_inputs)},
            outputs_summary={"n_seats": n_seats},
            started_at=t0,
        )

        # Step 2: unknown seats audit
        t0 = datetime.now(UTC)
        audit_result = None
        if input_data.observed_seats and input_data.net_buys:
            audit_result = unknown_seats_audit_loop(
                observed_seats=input_data.observed_seats,
                net_buys=input_data.net_buys,
                known_tycoon_seats=input_data.known_tycoon_seats,
                match_threshold=config.match_threshold,
                high_risk_net_buy_percentile=config.high_risk_percentile,
            )

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="unknown_seats_audit_loop",
            inputs_summary={"n_observed": len(input_data.observed_seats)},
            outputs_summary={"high_risk": audit_result.high_risk_count if audit_result else 0},
            started_at=t0,
        )

        # Step 3: sector returns (async oprim)
        t0 = datetime.now(UTC)
        sector_rows: list[dict[str, Any]] = []
        try:
            sector_list = asyncio.run(
                oprim.fetch_sector_returns(as_of_date=config.trade_date, top_n=10)
            )
            sector_rows = [
                s.model_dump() if hasattr(s, "model_dump") else dict(s) for s in sector_list
            ]
        except Exception:
            pass

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oprim",
            callable_name="fetch_sector_returns",
            inputs_summary={"trade_date": str(config.trade_date)},
            outputs_summary={"n_sectors": len(sector_rows)},
            started_at=t0,
        )

        findings = LhbPanelFindings(
            trade_date=str(config.trade_date),
            symbol_scope=config.symbol_scope,
            n_seats_analyzed=n_seats,
            top_seats_by_winrate=top_seats,
            high_risk_unknown_count=audit_result.high_risk_count if audit_result else 0,
            top_sector_returns=sector_rows,
            audit_summary={
                "total_observed": audit_result.total_observed if audit_result else 0,
                "matched_known": audit_result.matched_known if audit_result else 0,
            },
        )

    except Exception as exc:
        status = "failed"
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }

    finally:
        trail = build_decision_trail(
            fingerprint=fingerprint,
            config=config,
            input_data=input_data,
            trail_steps=trail_steps,
            cost_tracker=cost_tracker,
            started_at=started_at,
            status=status,
            error=error,
        )
        if output_dir:
            (output_dir / "decision_trail.json").write_text(
                json.dumps(trail, indent=2, default=str), encoding="utf-8"
            )

    return {
        "findings": findings.model_dump() if findings else None,
        "fingerprint": fingerprint,
        "decision_trail": trail,
        "status": status,
        "error": error,
    }
