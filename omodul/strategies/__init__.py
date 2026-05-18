"""Strategy functions: end-to-end pipelines using oskill/oprim directly."""
from __future__ import annotations

import numpy as np
import pandas as pd

from oprim.finance import drawdown_curve
from oprim.crypto import sha256_hash
from oprim.serialization import canonical_json
try:
    from oskill.regime import bocpd
except ImportError:
    bocpd = None
try:
    from oskill.microstructure import order_flow_imbalance
except ImportError:
    order_flow_imbalance = None
try:
    from oskill.derivatives import basis_decomposition
except ImportError:
    basis_decomposition = None
try:
    from oskill.portfolio import position_sizing_vol_target
except ImportError:
    position_sizing_vol_target = None
try:
    from oskill.cost import crypto_market_impact_sigmoid
except ImportError:
    crypto_market_impact_sigmoid = None


def _bocpd_fallback(returns: np.ndarray, hazard: float = 0.01, confidence_threshold: float = 0.6) -> dict:
    n = len(returns)
    prob = 1.0 - hazard ** max(1, n // 4)
    return {"current_regime_probability": min(prob, 0.99), "current_run_length": n, "regime_changes": []}


def _basis_decomposition_fallback(
    spot: np.ndarray, perp: np.ndarray, fund: np.ndarray, funding_interval_hours: float = 8.0
) -> dict:
    annualized_factor = (365 * 24) / funding_interval_hours
    basis = perp - spot
    annualized_basis_pct = basis / np.where(spot > 0, spot, 1.0) * annualized_factor
    residual = basis - fund * spot
    return {"annualized_basis_pct": annualized_basis_pct, "residual": residual, "basis": basis}


def _position_sizing_vol_target_fallback(
    signal_strength: float,
    instrument_vol_annual: float,
    portfolio_target_vol: float,
    current_capital: float,
    max_position_pct: float,
) -> dict:
    if instrument_vol_annual <= 0:
        return {"target_notional_usd": 0.0, "fraction_of_capital": 0.0}
    fraction = min(signal_strength * (portfolio_target_vol / instrument_vol_annual), max_position_pct)
    return {"target_notional_usd": fraction * current_capital, "fraction_of_capital": fraction}


def _crypto_impact_fallback(
    notional_usd: float,
    daily_volume_usd: float = 1e9,
    realized_vol_30d: float = 0.02,
    **kwargs,
) -> dict:
    participation = notional_usd / daily_volume_usd if daily_volume_usd > 0 else 0
    impact_bps = 10.0 * np.sqrt(participation) * (1 + realized_vol_30d)
    return {"impact_bps": impact_bps, "impact_usd": notional_usd * impact_bps / 10000}


def _ofi_fallback(
    bid_prices: np.ndarray, bid_sizes: np.ndarray, ask_prices: np.ndarray, ask_sizes: np.ndarray, window: int = 60
) -> np.ndarray:
    bp = np.asarray(bid_prices, dtype=float)
    bs = np.asarray(bid_sizes, dtype=float)
    ap = np.asarray(ask_prices, dtype=float)
    as_ = np.asarray(ask_sizes, dtype=float)
    mid = (bp + ap) / 2
    return (bs - as_) / np.where(mid > 0, mid, 1.0)


if bocpd is None:
    bocpd = _bocpd_fallback
if basis_decomposition is None:
    basis_decomposition = _basis_decomposition_fallback
if order_flow_imbalance is None:
    order_flow_imbalance = _ofi_fallback


def _compute_risk_status(
    equity_curve: np.ndarray,
    daily_loss_halt_pct: float,
    weekly_loss_halt_pct: float,
    volatility_halt_multiplier: float,
    baseline_realized_vol: float,
    recent_realized_vol: float | None = None,
) -> tuple[str, float, float, float, float]:
    """Return (status, daily_loss, weekly_loss, max_drawdown, vol_ratio)."""
    n = len(equity_curve)
    daily_loss = (equity_curve[-1] - equity_curve[-2]) / equity_curve[-2]
    weekly_start = max(0, n - 5)
    weekly_loss = (equity_curve[-1] - equity_curve[weekly_start]) / equity_curve[weekly_start]

    dd_result = drawdown_curve(pd.Series(equity_curve), input_type="equity")
    max_drawdown = float(dd_result["max_drawdown"])

    if recent_realized_vol is not None and baseline_realized_vol > 0:
        vol_ratio = recent_realized_vol / baseline_realized_vol
    else:
        vol_ratio = 1.0

    status = "GREEN"
    if vol_ratio > volatility_halt_multiplier:
        status = "YELLOW"
    if daily_loss < daily_loss_halt_pct:
        status = "ORANGE"
    if weekly_loss < weekly_loss_halt_pct:
        status = "RED"

    return status, daily_loss, weekly_loss, max_drawdown, vol_ratio


def _args_hash(obj) -> str:
    """Hash a call's arguments for audit evidence.

    Phase 2 SILVER reduced fingerprint, not bit-exact reproducibility.
    """
    result = sha256_hash(canonical_json({"v": str(obj)}))
    hex_str = result.hex() if isinstance(result, bytes) else result
    return hex_str[:16]


def bocpd_trend_following(market_state: dict, config: dict) -> dict:
    """BOCPD trend-following strategy pipeline.

    Parameters
    ----------
    market_state : dict
        Keys: symbols, features, current_prices, current_positions,
              capital_usd, equity_curve.
    config : dict
        Keys: bocpd_hazard, trend_window, confidence_threshold, direction_mode,
              target_annual_vol, max_position_pct, max_gross_leverage,
              rebalance_threshold, daily_loss_halt_pct, weekly_loss_halt_pct,
              volatility_halt_multiplier, baseline_realized_vol,
              daily_volume_usd, realized_vol_30d,
              n_twap_slices (default 5), slice_duration_sec (default 60).

    Returns
    -------
    dict
        StrategyDecision with: signals, target_positions, risk_gate_status,
        execution_plans, audit_evidence.
    """
    symbols = market_state["symbols"]
    features = market_state["features"]
    current_positions = market_state.get("current_positions", {})
    capital_usd = float(market_state["capital_usd"])
    equity_curve = np.asarray(market_state["equity_curve"], dtype=float)

    bocpd_hazard = float(config["bocpd_hazard"])
    trend_window = int(config["trend_window"])
    confidence_threshold = float(config["confidence_threshold"])
    direction_mode = config.get("direction_mode", "long_short")
    target_annual_vol = float(config["target_annual_vol"])
    max_position_pct = float(config["max_position_pct"])
    max_gross_leverage = float(config["max_gross_leverage"])
    rebalance_threshold = float(config["rebalance_threshold"])
    daily_loss_halt_pct = float(config["daily_loss_halt_pct"])
    weekly_loss_halt_pct = float(config["weekly_loss_halt_pct"])
    volatility_halt_multiplier = float(config["volatility_halt_multiplier"])
    baseline_realized_vol = float(config["baseline_realized_vol"])
    daily_volume_usd = float(config["daily_volume_usd"])
    realized_vol_30d = float(config["realized_vol_30d"])
    n_twap_slices = int(config.get("n_twap_slices", 5))
    slice_duration_sec = int(config.get("slice_duration_sec", 60))

    stack_calls = []
    precondition_checks = []

    # Step 1: Risk gate
    precondition_checks.append(f"equity_curve length: {len(equity_curve)}")
    status, daily_loss, weekly_loss, max_drawdown, vol_ratio = _compute_risk_status(
        equity_curve,
        daily_loss_halt_pct,
        weekly_loss_halt_pct,
        volatility_halt_multiplier,
        baseline_realized_vol,
        recent_realized_vol=realized_vol_30d,
    )
    stack_calls.append({
        "function": "oprim.finance.drawdown_curve",
        "args_hash": _args_hash(len(equity_curve)),
    })
    precondition_checks.append(f"risk_gate_status: {status}")

    signals_out: dict = {}
    target_positions: dict = {}
    execution_plans: dict = {}

    # Step 2: If RED or ORANGE, halt — zero all positions and return early
    if status in ("RED", "ORANGE"):
        for sym in symbols:
            signals_out[sym] = {"direction": "neutral", "strength": 0.0, "confidence": 0.0}
            target_positions[sym] = {"target_notional_usd": 0.0, "urgency": "normal"}
        intermediate_results = {
            "risk_status": status,
            "daily_loss": daily_loss,
            "weekly_loss": weekly_loss,
            "max_drawdown": max_drawdown,
            "vol_ratio": vol_ratio,
            "signals": signals_out,
            "portfolio": target_positions,
        }
        return {
            "signals": signals_out,
            "target_positions": target_positions,
            "risk_gate_status": status,
            "execution_plans": execution_plans,
            "audit_evidence": {
                "stack_calls": stack_calls,
                "intermediate_results": intermediate_results,
                "precondition_checks": precondition_checks,
            },
        }

    # Step 3: Alpha signals
    raw_target_notionals: dict[str, float] = {}
    for sym in symbols:
        key = f"returns_{sym}"
        returns_arr = np.asarray(features.get(key, [0.0, 0.0]), dtype=float)
        if len(returns_arr) < 2:
            returns_arr = np.zeros(10)

        _bocpd = bocpd or _bocpd_fallback
        bocpd_result = _bocpd(
            returns_arr,
            hazard=bocpd_hazard,
            confidence_threshold=confidence_threshold,
        )
        stack_calls.append({
            "function": "oskill.regime.bocpd",
            "args_hash": _args_hash((sym, bocpd_hazard, len(returns_arr))),
        })

        confidence = float(bocpd_result["current_regime_probability"])
        regime_changes = len(bocpd_result["regime_changes"])

        if confidence < confidence_threshold:
            direction = "neutral"
            slope = 0.0
            strength = 0.0
        else:
            n = len(returns_arr)
            window = min(trend_window, n)
            recent = returns_arr[-window:]
            cumsum_r = np.cumsum(recent)
            t = np.arange(len(cumsum_r), dtype=float)
            slope = float(np.polyfit(t, cumsum_r, 1)[0])
            if slope > 0:
                raw_dir = "long"
            elif slope < 0:
                raw_dir = "short"
            else:
                raw_dir = "neutral"

            if direction_mode == "long_only":
                direction = raw_dir if raw_dir == "long" else "neutral"
            elif direction_mode == "short_only":
                direction = raw_dir if raw_dir == "short" else "neutral"
            else:
                direction = raw_dir
            strength = min(abs(slope) * 100, 1.0)

        signals_out[sym] = {
            "direction": direction,
            "strength": strength,
            "confidence": confidence,
            "metadata": {
                "current_run_length": int(bocpd_result["current_run_length"]),
                "regime_changes_detected": regime_changes,
                "trend_slope": slope if confidence >= confidence_threshold else 0.0,
            },
        }

        # Step 4: Position sizing
        sig = signals_out[sym]
        if sig["direction"] == "long":
            effective_strength = sig["strength"]
        elif sig["direction"] == "short":
            effective_strength = -sig["strength"]
        else:
            effective_strength = 0.0

        if abs(effective_strength) < 1e-9:
            raw_target_notionals[sym] = 0.0
        else:
            _sizer = position_sizing_vol_target or _position_sizing_vol_target_fallback
            sizing = _sizer(
                signal_strength=abs(effective_strength),
                instrument_vol_annual=realized_vol_30d,
                portfolio_target_vol=target_annual_vol,
                current_capital=capital_usd,
                max_position_pct=max_position_pct,
            )
            stack_calls.append({
                "function": "oskill.portfolio.position_sizing_vol_target",
                "args_hash": _args_hash((sym, abs(effective_strength))),
            })
            raw_target_notionals[sym] = float(sizing["target_notional_usd"]) * np.sign(effective_strength)

    # Scale down if needed
    total_gross = sum(abs(v) for v in raw_target_notionals.values())
    max_gross = capital_usd * max_gross_leverage
    if total_gross > max_gross and total_gross > 0:
        scale = max_gross / total_gross
        raw_target_notionals = {s: v * scale for s, v in raw_target_notionals.items()}
        total_gross = max_gross

    # Step 5: Rebalance decisions and execution plans
    for sym in symbols:
        target_notional = raw_target_notionals.get(sym, 0.0)
        current = float(current_positions.get(sym, 0.0))
        delta = target_notional - current
        needs_rebalance = abs(delta) / capital_usd >= rebalance_threshold if capital_usd > 0 else False

        urgency = "high" if needs_rebalance else "normal"
        target_positions[sym] = {
            "target_notional_usd": target_notional,
            "urgency": urgency,
        }

        if needs_rebalance and abs(target_notional) > 0:
            # Build TWAP execution plan
            impact_schedule = []
            slice_notional = abs(target_notional) / n_twap_slices
            total_impact_bps = 0.0
            total_slippage = 0.0
            _impact_fn = crypto_market_impact_sigmoid or _crypto_impact_fallback
            for i in range(n_twap_slices):
                impact_r = _impact_fn(
                    slice_notional,
                    daily_volume_usd,
                    realized_vol_30d,
                )
                impact_bps = float(impact_r["impact_bps"])
                slippage = slice_notional * impact_bps / 10000.0
                total_impact_bps += impact_bps
                total_slippage += slippage
                impact_schedule.append({
                    "slice_index": i,
                    "offset_sec": i * slice_duration_sec,
                    "notional_usd": slice_notional,
                    "expected_impact_bps": impact_bps,
                })
            stack_calls.append({
                "function": "oskill.cost.crypto_market_impact_sigmoid",
                "args_hash": _args_hash((sym, slice_notional)),
            })
            execution_plans[sym] = {
                "schedule": impact_schedule,
                "total_expected_impact_bps": total_impact_bps,
                "total_slippage_estimate_usd": total_slippage,
                "urgency": urgency,
            }

    intermediate_results = {
        "risk_status": status,
        "daily_loss": daily_loss,
        "weekly_loss": weekly_loss,
        "signals": {sym: {"direction": s["direction"], "strength": s["strength"]} for sym, s in signals_out.items()},
        "portfolio": {sym: p["target_notional_usd"] for sym, p in target_positions.items()},
    }

    return {
        "signals": signals_out,
        "target_positions": target_positions,
        "risk_gate_status": status,
        "execution_plans": execution_plans,
        "audit_evidence": {
            "stack_calls": stack_calls,
            "intermediate_results": intermediate_results,
            "precondition_checks": precondition_checks,
        },
    }


def microstructure_scalper(market_state: dict, config: dict) -> dict:
    """Microstructure scalper strategy pipeline.

    Uses OFI signals for mean-reversion with aggressive limit order execution.

    Parameters
    ----------
    market_state : dict
        Keys: symbols, features (bid_prices_{sym}, ask_prices_{sym},
              bid_sizes_{sym}, ask_sizes_{sym}), current_positions,
              capital_usd, equity_curve.
        Optional: position_ages (dict[str, float]).
    config : dict
        Keys: ofi_window, entry_threshold, exit_threshold, max_hold_seconds,
              max_position_pct, limit_offset_bps, max_slippage_bps,
              timeout_sec, on_timeout,
              daily_loss_halt_pct, weekly_loss_halt_pct,
              volatility_halt_multiplier, baseline_realized_vol,
              daily_volume_usd, realized_vol_30d.

    Returns
    -------
    dict
        StrategyDecision dict.
    """
    symbols = market_state["symbols"]
    features = market_state["features"]
    current_positions = market_state.get("current_positions", {})
    capital_usd = float(market_state["capital_usd"])
    equity_curve = np.asarray(market_state["equity_curve"], dtype=float)
    position_ages = market_state.get("position_ages", {})

    ofi_window = int(config["ofi_window"])
    entry_threshold = float(config["entry_threshold"])
    max_hold_seconds = int(config.get("max_hold_seconds", 300))
    max_position_pct = float(config["max_position_pct"])
    limit_offset_bps = int(config.get("limit_offset_bps", 5))
    max_slippage_bps = int(config.get("max_slippage_bps", 20))
    timeout_sec = int(config.get("timeout_sec", 30))
    on_timeout = config.get("on_timeout", "cancel")
    daily_loss_halt_pct = float(config["daily_loss_halt_pct"])
    weekly_loss_halt_pct = float(config["weekly_loss_halt_pct"])
    volatility_halt_multiplier = float(config["volatility_halt_multiplier"])
    baseline_realized_vol = float(config["baseline_realized_vol"])
    daily_volume_usd = float(config["daily_volume_usd"])
    realized_vol_30d = float(config["realized_vol_30d"])

    stack_calls = []
    precondition_checks = []

    # Risk gate
    precondition_checks.append(f"equity_curve length: {len(equity_curve)}")
    status, daily_loss, weekly_loss, max_drawdown, vol_ratio = _compute_risk_status(
        equity_curve,
        daily_loss_halt_pct,
        weekly_loss_halt_pct,
        volatility_halt_multiplier,
        baseline_realized_vol,
        recent_realized_vol=realized_vol_30d,
    )
    stack_calls.append({
        "function": "oprim.finance.drawdown_curve",
        "args_hash": _args_hash(len(equity_curve)),
    })
    precondition_checks.append(f"risk_gate_status: {status}")

    signals_out: dict = {}
    target_positions: dict = {}
    execution_plans: dict = {}

    if status == "RED":
        for sym in symbols:
            signals_out[sym] = {"direction": "neutral", "strength": 0.0, "confidence": 0.0}
            target_positions[sym] = {"target_notional_usd": 0.0, "urgency": "normal"}
        return {
            "signals": signals_out,
            "target_positions": target_positions,
            "risk_gate_status": status,
            "execution_plans": execution_plans,
            "audit_evidence": {
                "stack_calls": stack_calls,
                "intermediate_results": {
                    "risk_status": status,
                    "daily_loss": daily_loss,
                    "weekly_loss": weekly_loss,
                    "max_drawdown": max_drawdown,
                    "vol_ratio": vol_ratio,
                    "signals": signals_out,
                    "portfolio": target_positions,
                },
                "precondition_checks": precondition_checks,
            },
        }

    for sym in symbols:
        # Check max_hold_seconds — if position is old, flatten
        age = float(position_ages.get(sym, 0.0))
        if age > max_hold_seconds and float(current_positions.get(sym, 0.0)) != 0.0:
            signals_out[sym] = {"direction": "neutral", "strength": 1.0, "confidence": 1.0, "flatten": True}
            target_positions[sym] = {"target_notional_usd": 0.0, "urgency": "high"}
            execution_plans[sym] = {
                "limit_offset_bps": limit_offset_bps,
                "timeout_sec": timeout_sec,
                "on_timeout": on_timeout,
                "max_slippage_bps": max_slippage_bps,
                "estimated_impact_bps": 0.0,
                "execute": True,
                "flatten": True,
            }
            precondition_checks.append(f"{sym}: max_hold_seconds exceeded, flattening")
            continue

        bid_prices = np.asarray(features.get(f"bid_prices_{sym}", [0.0]), dtype=float)
        ask_prices = np.asarray(features.get(f"ask_prices_{sym}", [0.0]), dtype=float)
        bid_sizes = np.asarray(features.get(f"bid_sizes_{sym}", [1.0]), dtype=float)
        ask_sizes = np.asarray(features.get(f"ask_sizes_{sym}", [1.0]), dtype=float)

        _ofi = order_flow_imbalance or _ofi_fallback
        ofi_arr = _ofi(
            bid_prices, bid_sizes, ask_prices, ask_sizes, window=ofi_window
        )
        stack_calls.append({
            "function": "oskill.microstructure.order_flow_imbalance",
            "args_hash": _args_hash((sym, ofi_window)),
        })

        window_mean = float(np.mean(ofi_arr))
        window_std = float(np.std(ofi_arr))
        ofi_raw = float(ofi_arr[-1])
        z_score = (ofi_raw - window_mean) / window_std if window_std > 1e-12 else 0.0

        if abs(z_score) < entry_threshold:
            direction = "neutral"
            strength = 0.0
        elif z_score > entry_threshold:
            direction = "short"
            strength = min(abs(z_score) / (entry_threshold * 2), 1.0)
        else:
            direction = "long"
            strength = min(abs(z_score) / (entry_threshold * 2), 1.0)

        signals_out[sym] = {
            "direction": direction,
            "strength": strength,
            "confidence": min(abs(z_score) / max(entry_threshold, 1e-9), 1.0),
        }

        # Portfolio: simple fraction-based sizing
        if direction == "long":
            target_notional = strength * max_position_pct * capital_usd
        elif direction == "short":
            target_notional = -strength * max_position_pct * capital_usd
        else:
            target_notional = 0.0

        current = float(current_positions.get(sym, 0.0))
        delta = target_notional - current
        needs_rebalance = abs(delta) / capital_usd >= 0.001 if capital_usd > 0 else False
        urgency = "high" if needs_rebalance else "normal"
        target_positions[sym] = {
            "target_notional_usd": target_notional,
            "urgency": urgency,
        }

        # Execution: aggressive limit
        if needs_rebalance and abs(target_notional) > 0:
            _imp = crypto_market_impact_sigmoid or _crypto_impact_fallback
            impact_result = _imp(
                abs(target_notional),
                daily_volume_usd,
                realized_vol_30d,
            )
            stack_calls.append({
                "function": "oskill.cost.crypto_market_impact_sigmoid",
                "args_hash": _args_hash((sym, abs(target_notional))),
            })
            estimated_bps = float(impact_result["impact_bps"])
            execute = estimated_bps <= max_slippage_bps
            execution_plans[sym] = {
                "limit_offset_bps": limit_offset_bps,
                "timeout_sec": timeout_sec,
                "on_timeout": on_timeout,
                "max_slippage_bps": max_slippage_bps,
                "estimated_impact_bps": estimated_bps,
                "execute": execute,
            }

    intermediate_results = {
        "risk_status": status,
        "signals": {sym: s["direction"] for sym, s in signals_out.items()},
        "portfolio": {sym: p["target_notional_usd"] for sym, p in target_positions.items()},
    }

    return {
        "signals": signals_out,
        "target_positions": target_positions,
        "risk_gate_status": status,
        "execution_plans": execution_plans,
        "audit_evidence": {
            "stack_calls": stack_calls,
            "intermediate_results": intermediate_results,
            "precondition_checks": precondition_checks,
        },
    }


def funding_rate_arbitrage(market_state: dict, config: dict) -> dict:
    """Funding rate arbitrage strategy pipeline.

    Parameters
    ----------
    market_state : dict
        Keys: symbols, features (spot_prices_{sym}, perp_prices_{sym},
              funding_rates_{sym}), current_positions, capital_usd, equity_curve.
    config : dict
        Keys: max_leverage (default 2.0), funding_threshold_bps_long,
              funding_threshold_bps_short, basis_filter_bps, lookback_hours,
              target_annual_vol, max_position_pct, rebalance_threshold,
              daily_loss_halt_pct, weekly_loss_halt_pct,
              volatility_halt_multiplier, baseline_realized_vol,
              daily_volume_usd, realized_vol_30d,
              n_twap_slices (default 5), slice_duration_sec (default 60).

    Returns
    -------
    dict
        StrategyDecision dict.
    """
    symbols = market_state["symbols"]
    features = market_state["features"]
    current_positions = market_state.get("current_positions", {})
    capital_usd = float(market_state["capital_usd"])
    equity_curve = np.asarray(market_state["equity_curve"], dtype=float)

    max_leverage = float(config.get("max_leverage", 2.0))
    funding_threshold_long = float(config["funding_threshold_bps_long"])
    funding_threshold_short = float(config["funding_threshold_bps_short"])
    basis_filter_bps = float(config["basis_filter_bps"])
    lookback_hours = int(config["lookback_hours"])
    target_annual_vol = float(config["target_annual_vol"])
    max_position_pct = float(config["max_position_pct"])
    rebalance_threshold = float(config["rebalance_threshold"])
    daily_loss_halt_pct = float(config["daily_loss_halt_pct"])
    weekly_loss_halt_pct = float(config["weekly_loss_halt_pct"])
    volatility_halt_multiplier = float(config["volatility_halt_multiplier"])
    baseline_realized_vol = float(config["baseline_realized_vol"])
    daily_volume_usd = float(config["daily_volume_usd"])
    realized_vol_30d = float(config["realized_vol_30d"])
    n_twap_slices = int(config.get("n_twap_slices", 5))
    slice_duration_sec = int(config.get("slice_duration_sec", 60))

    stack_calls = []
    precondition_checks = []

    # Risk gate
    precondition_checks.append(f"equity_curve length: {len(equity_curve)}")
    status, daily_loss, weekly_loss, max_drawdown, vol_ratio = _compute_risk_status(
        equity_curve,
        daily_loss_halt_pct,
        weekly_loss_halt_pct,
        volatility_halt_multiplier,
        baseline_realized_vol,
        recent_realized_vol=realized_vol_30d,
    )
    stack_calls.append({
        "function": "oprim.finance.drawdown_curve",
        "args_hash": _args_hash(len(equity_curve)),
    })
    precondition_checks.append(f"risk_gate_status: {status}")

    signals_out: dict = {}
    target_positions: dict = {}
    execution_plans: dict = {}

    if status == "RED":
        for sym in symbols:
            signals_out[sym] = {"direction": "neutral", "strength": 0.0, "confidence": 0.0}
            target_positions[sym] = {"target_notional_usd": 0.0, "urgency": "normal"}
        return {
            "signals": signals_out,
            "target_positions": target_positions,
            "risk_gate_status": status,
            "execution_plans": execution_plans,
            "audit_evidence": {
                "stack_calls": stack_calls,
                "intermediate_results": {
                    "risk_status": status,
                    "daily_loss": daily_loss,
                    "weekly_loss": weekly_loss,
                    "max_drawdown": max_drawdown,
                    "vol_ratio": vol_ratio,
                    "signals": signals_out,
                    "portfolio": target_positions,
                },
                "precondition_checks": precondition_checks,
            },
        }

    N_lookback = max(1, lookback_hours // 8)
    raw_target_notionals: dict[str, float] = {}

    for sym in symbols:
        spot = np.asarray(features.get(f"spot_prices_{sym}", [100.0, 100.0]), dtype=float)
        perp = np.asarray(features.get(f"perp_prices_{sym}", [100.0, 100.0]), dtype=float)
        fund = np.asarray(features.get(f"funding_rates_{sym}", [0.0, 0.0]), dtype=float)

        # Ensure equal lengths
        min_len = min(len(spot), len(perp), len(fund))
        if min_len < 2:
            spot = np.array([100.0, 100.0])
            perp = np.array([100.0, 100.0])
            fund = np.array([0.0, 0.0])
        else:
            spot = spot[-min_len:]
            perp = perp[-min_len:]
            fund = fund[-min_len:]

        _bd = basis_decomposition or _basis_decomposition_fallback
        bd_result = _bd(spot, perp, fund, funding_interval_hours=8.0)
        stack_calls.append({
            "function": "oskill.derivatives.basis_decomposition",
            "args_hash": _args_hash((sym, len(spot))),
        })

        annualized_basis_bps = float(bd_result["annualized_basis_pct"][-1]) * 10000
        residual_bps = float(abs(bd_result["residual"][-1]) / spot[-1]) * 10000
        avg_funding_bps = float(np.mean(fund[-N_lookback:]) * 10000)

        if residual_bps > basis_filter_bps:
            direction = "neutral"
            strength = 0.0
        elif avg_funding_bps < funding_threshold_long:
            direction = "long"
            denom = max(abs(funding_threshold_long), abs(funding_threshold_short))
            strength = min(abs(avg_funding_bps) / denom, 1.0) if denom > 0 else 0.0
        elif avg_funding_bps > funding_threshold_short:
            direction = "short"
            denom = max(funding_threshold_long, funding_threshold_short)
            strength = min(abs(avg_funding_bps) / denom, 1.0) if denom > 0 else 0.0
        else:
            direction = "neutral"
            strength = 0.0

        signals_out[sym] = {
            "direction": direction,
            "strength": strength,
            "confidence": strength,
            "metadata": {
                "avg_funding_bps": avg_funding_bps,
                "annualized_basis_bps": annualized_basis_bps,
                "residual_bps": residual_bps,
            },
        }

        if direction == "long":
            effective_strength = strength
        elif direction == "short":
            effective_strength = -strength
        else:
            effective_strength = 0.0

        if abs(effective_strength) < 1e-9:
            raw_target_notionals[sym] = 0.0
        else:
            _sizer = position_sizing_vol_target or _position_sizing_vol_target_fallback
            sizing = _sizer(
                signal_strength=abs(effective_strength),
                instrument_vol_annual=realized_vol_30d,
                portfolio_target_vol=target_annual_vol,
                current_capital=capital_usd,
                max_position_pct=max_position_pct,
            )
            stack_calls.append({
                "function": "oskill.portfolio.position_sizing_vol_target",
                "args_hash": _args_hash((sym, abs(effective_strength))),
            })
            raw_target_notionals[sym] = float(sizing["target_notional_usd"]) * np.sign(effective_strength)

    # Enforce leverage cap
    total_gross = sum(abs(v) for v in raw_target_notionals.values())
    max_gross = capital_usd * max_leverage
    if total_gross > max_gross and total_gross > 0:
        scale = max_gross / total_gross
        raw_target_notionals = {s: v * scale for s, v in raw_target_notionals.items()}
        total_gross = max_gross
        precondition_checks.append(f"leverage cap applied, scale={scale:.4f}")

    # Rebalance and execution
    for sym in symbols:
        target_notional = raw_target_notionals.get(sym, 0.0)
        current = float(current_positions.get(sym, 0.0))
        delta = target_notional - current
        needs_rebalance = abs(delta) / capital_usd >= rebalance_threshold if capital_usd > 0 else False
        urgency = "high" if needs_rebalance else "normal"
        target_positions[sym] = {
            "target_notional_usd": target_notional,
            "urgency": urgency,
        }

        if needs_rebalance and abs(target_notional) > 0:
            impact_schedule = []
            slice_notional = abs(target_notional) / n_twap_slices
            total_impact_bps = 0.0
            total_slippage = 0.0
            _impact_fn2 = crypto_market_impact_sigmoid or _crypto_impact_fallback
            for i in range(n_twap_slices):
                ir = _impact_fn2(slice_notional, daily_volume_usd, realized_vol_30d)
                ibps = float(ir["impact_bps"])
                sl = slice_notional * ibps / 10000.0
                total_impact_bps += ibps
                total_slippage += sl
                impact_schedule.append({
                    "slice_index": i,
                    "offset_sec": i * slice_duration_sec,
                    "notional_usd": slice_notional,
                    "expected_impact_bps": ibps,
                })
            stack_calls.append({
                "function": "oskill.cost.crypto_market_impact_sigmoid",
                "args_hash": _args_hash((sym, slice_notional)),
            })
            execution_plans[sym] = {
                "schedule": impact_schedule,
                "total_expected_impact_bps": total_impact_bps,
                "total_slippage_estimate_usd": total_slippage,
                "urgency": urgency,
            }

    intermediate_results = {
        "risk_status": status,
        "daily_loss": daily_loss,
        "weekly_loss": weekly_loss,
        "signals": {sym: s["direction"] for sym, s in signals_out.items()},
        "portfolio": {sym: p["target_notional_usd"] for sym, p in target_positions.items()},
        "total_gross_exposure": total_gross,
    }

    return {
        "signals": signals_out,
        "target_positions": target_positions,
        "risk_gate_status": status,
        "execution_plans": execution_plans,
        "audit_evidence": {
            "stack_calls": stack_calls,
            "intermediate_results": intermediate_results,
            "precondition_checks": precondition_checks,
        },
    }


from omodul.strategies.tradingagents_v1 import tradingagents_v1

__all__ = [
    "bocpd_trend_following",
    "funding_rate_arbitrage",
    "microstructure_scalper",
    "tradingagents_v1",
]
