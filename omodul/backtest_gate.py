"""omodul.backtest_gate — Gate a strategy before live deployment.

Pillars: decision_trail, report
Composites: oskill.walk_forward + oprim.pbo_compute + oprim.deflated_sharpe

⚠️  OOS deflated_sharpe ≤ 0 OR PBO > 0.5 → status = "failed"
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from omodul._base import BaseConfig, Trail, build_result


class BacktestGateConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "backtest_gate"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "report"}
    _fingerprint_fields: ClassVar[set[str]] = {"strategy_name", "n_splits"}

    strategy_name: str
    n_splits: int = 5
    embargo: int = 0
    pbo_threshold: float = 0.5
    periods: int = 252


def backtest_gate(
    strategy_fn: Callable[[list, list], dict[str, Any]],
    data: Any,
    *,
    config: BacktestGateConfig,
) -> dict[str, Any]:
    """Walk-forward validation with DSR and PBO gates.

    Composites:
        1. oskill.walk_forward   — CPCV splits + deflated Sharpe.
        2. oprim.pbo_compute     — probability of backtest overfitting.
        3. oprim.deflated_sharpe — OOS DSR (invoked inside walk_forward).

    Failure conditions (any one is sufficient):
        - OOS deflated_sharpe ≤ 0
        - PBO > pbo_threshold (default 0.5)

    Args:
        strategy_fn: ``(train_data, test_data) -> {"sharpe": float, ...}``
        data: Full dataset.
        config: BacktestGateConfig.

    Returns:
        Result with ``status``, ``deflated_sharpe``, ``pbo``,
        ``mean_oos_sharpe``, ``fail_reasons``, ``walk_forward_result``.
    """
    from oprim.pbo_compute import pbo_compute  # noqa: PLC0415
    from oskill.walk_forward import walk_forward  # noqa: PLC0415

    trail = Trail()

    wf = walk_forward(
        strategy_fn, data,
        n_splits=config.n_splits,
        embargo=config.embargo,
        periods=config.periods,
    )
    trail.record(event="walk_forward_done",
                 n_splits=config.n_splits,
                 mean_oos_sharpe=wf["mean_oos_sharpe"])

    oos_sharpes = wf["oos_sharpes"]
    dsr_value = wf["deflated_sharpe"].get("deflated_sharpe", 0.0)

    is_sharpes = [fr.get("is_sharpe", fr.get("sharpe", 0.0)) for fr in wf["fold_results"]]
    pbo_result = pbo_compute(is_sharpes, oos_sharpes)
    pbo_value = pbo_result["pbo"]
    trail.record(event="pbo_computed", pbo=pbo_value)

    fail_reasons: list[str] = []
    if dsr_value <= 0:
        fail_reasons.append(f"deflated_sharpe={dsr_value:.4f} <= 0")
    if pbo_value > config.pbo_threshold:
        fail_reasons.append(f"PBO={pbo_value:.4f} > {config.pbo_threshold}")

    status = "failed" if fail_reasons else "passed"
    trail.record(event="gate_decision", status=status)

    report = "\n".join([
        f"Strategy: {config.strategy_name}",
        f"Status: {status}",
        f"Mean OOS Sharpe: {wf['mean_oos_sharpe']:.4f}",
        f"Deflated Sharpe: {dsr_value:.4f}",
        f"PBO: {pbo_value:.4f}",
        *([f"Fail reasons: " + "; ".join(fail_reasons)] if fail_reasons else []),
    ])

    return build_result(
        status="ok",
        trail=trail,
        cost_usd=0.0,
        gate_status=status,
        deflated_sharpe=dsr_value,
        pbo=pbo_value,
        mean_oos_sharpe=wf["mean_oos_sharpe"],
        fail_reasons=fail_reasons,
        walk_forward_result=wf,
        report=report,
    )
