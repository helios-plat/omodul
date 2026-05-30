"""omodul.discipline_banner_toast_data — 纪律看板/Toast 数据.

Pillars: fingerprint + decision_trail
H1 compliant: calls oskills/oprims only (no sibling omodul).
Composition (B10 oskill + B8 oprim):
  - oskill.discipline_vs_violation_winrate_compute  (sync)
  - oprim.stop_loss_compliance_check                (sync, per-trade compliance)
"""

from __future__ import annotations

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


class DisciplineBannerConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "discipline_banner_toast_data"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[Set[str]] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[Set[str]] = {"user_id_hash", "trade_date"}

    user_id_hash: str
    trade_date: date
    stop_loss_threshold_pct: float = -5.0


class DisciplineBannerInput(BaseModel):
    trade_records: list[dict[str, Any]] = Field(
        default_factory=list,
        description="TradeRecord-compatible dicts: return_pct, is_compliant",
    )
    current_open_trades: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Open positions: symbol, entry_price, current_price, stop_loss_price",
    )


class DisciplineBannerFindings(BaseModel):
    user_id_hash: str
    trade_date: str
    discipline_win_rate: float = 0.0
    violation_win_rate: float = 0.0
    discipline_avg_return: float = 0.0
    violation_avg_return: float = 0.0
    n_compliant: int = 0
    n_violating: int = 0
    open_trades_breach_count: int = 0
    open_trades_breach_symbols: list[str] = Field(default_factory=list)
    banner_severity: str = "green"
    toast_message: str = ""


def compute_fingerprint_for(
    config: DisciplineBannerConfig,
    input_data: DisciplineBannerInput,
) -> str:
    return compute_fingerprint(config, input_data)


def discipline_banner_toast_data(
    config: DisciplineBannerConfig,
    input_data: DisciplineBannerInput,
    output_dir: Path | None = None,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Compute discipline banner + toast payload for UI.

    Returns dict with: findings, fingerprint, decision_trail, status, error.
    """
    from oskill.discipline_vs_violation_winrate_compute import (
        TradeRecord,
        discipline_vs_violation_winrate_compute,
    )
    import oprim

    started_at = datetime.now(UTC)
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    cost_tracker = CostTracker()
    trail_steps: list[dict[str, Any]] = []
    fingerprint = compute_fingerprint_for(config, input_data)

    findings: DisciplineBannerFindings | None = None
    error: dict[str, Any] | None = None
    status = "completed"
    trail: dict[str, Any] = {}

    try:
        # Step 1: discipline vs violation winrate
        t0 = datetime.now(UTC)
        records: list[TradeRecord] = []
        for r in input_data.trade_records:
            try:
                records.append(TradeRecord(**r))
            except Exception:
                pass

        disc_result = None
        if records:
            disc_result = discipline_vs_violation_winrate_compute(trade_records=records)

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="discipline_vs_violation_winrate_compute",
            inputs_summary={"n_records": len(records)},
            outputs_summary={
                "disc_win_rate": disc_result.discipline.win_rate if disc_result else 0,
                "viol_win_rate": disc_result.violation.win_rate if disc_result else 0,
            },
            started_at=t0,
        )

        # Step 2: stop_loss_compliance_check on open trades
        t0 = datetime.now(UTC)
        breach_symbols: list[str] = []
        for trade in input_data.current_open_trades:
            try:
                sl_result = oprim.stop_loss_compliance_check(
                    entry_price=float(trade["entry_price"]),
                    current_price=float(trade["current_price"]),
                    stop_loss_pct=float(trade.get("stop_loss_pct", 5.0)),
                )
                if sl_result.triggered:
                    breach_symbols.append(str(trade.get("symbol", "?")))
            except Exception:
                pass

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oprim",
            callable_name="stop_loss_compliance_check",
            inputs_summary={"n_open_trades": len(input_data.current_open_trades)},
            outputs_summary={"breach_count": len(breach_symbols)},
            started_at=t0,
        )

        # Step 3: compute banner severity + toast message
        n_compliant = disc_result.discipline.count if disc_result else 0
        n_violating = disc_result.violation.count if disc_result else 0
        disc_wr = disc_result.discipline.win_rate if disc_result else 0.0
        viol_wr = disc_result.violation.win_rate if disc_result else 0.0

        if breach_symbols or (n_violating > n_compliant and n_compliant + n_violating > 0):
            banner_severity = "red"
        elif n_violating > 0:
            banner_severity = "yellow"
        else:
            banner_severity = "green"

        if breach_symbols:
            toast_msg = f"⚠️ {len(breach_symbols)} 笔触发止损: {', '.join(breach_symbols[:3])}"
        elif disc_wr > viol_wr and n_compliant > 0:
            toast_msg = f"✅ 纪律执行率优秀 (胜率差 +{disc_wr - viol_wr:.1%})"
        else:
            toast_msg = "📊 纪律数据已更新"

        findings = DisciplineBannerFindings(
            user_id_hash=config.user_id_hash,
            trade_date=str(config.trade_date),
            discipline_win_rate=disc_result.discipline.win_rate if disc_result else 0.0,
            violation_win_rate=disc_result.violation.win_rate if disc_result else 0.0,
            discipline_avg_return=disc_result.discipline.avg_return_pct if disc_result else 0.0,
            violation_avg_return=disc_result.violation.avg_return_pct if disc_result else 0.0,
            n_compliant=n_compliant,
            n_violating=n_violating,
            open_trades_breach_count=len(breach_symbols),
            open_trades_breach_symbols=breach_symbols,
            banner_severity=banner_severity,
            toast_message=toast_msg,
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
