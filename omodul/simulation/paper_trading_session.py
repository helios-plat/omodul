"""Paper trading session with realistic market rules."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import oprim

STABILITY = "experimental"


def paper_trading_session(
    account_state: dict,
    orders: list[dict],
    ohlcv: dict[str, dict],
    market_rules: dict,
    timestamp: datetime,
) -> dict:
    """Execute a paper trading session: validate orders, apply rules, update state.

    Parameters
    ----------
    account_state : {"cash": float, "positions": [{"symbol": str, "qty": float,
                    "entry_price": float, "entry_date": date}], "history": [...]}
    orders : [{"order_id": str, "symbol": str, "side": "buy"/"sell",
               "quantity": float, "order_type": "market"/"limit",
               "price": float | None}]
    ohlcv : {symbol: {"open": float, "high": float, "low": float, "close": float}}
    market_rules : {
        "daily_limit": {"get_limit_pct": callable(symbol) -> float},
        "t_plus_n": int,
        "commission": {"rate": float, "min_fee": float},
        "stamp_tax": {"rate": float, "direction": str}
    }
    timestamp : current session timestamp

    Returns
    -------
    {
        "executed_trades": [...],
        "rejected_orders": [...],
        "new_account_state": dict,
        "pnl_delta": float
    }

    Methodology
    -----------
    For each order:
    1. Check T+N rule (if sell, holding age sufficient?)
    2. Check daily limit (block buy at limit-up, block sell at limit-down)
    3. Compute fill price (market: open; limit: check if hit)
    4. Apply commission + stamp tax
    5. Update positions and cash

    Uses: oprim.markets.limits, oprim.markets.rules

    Reference
    ---------
    Standard paper trading simulator pattern.
    """
    cash = float(account_state.get("cash", 0.0))
    positions: dict[str, dict] = {p["symbol"]: dict(p) for p in account_state.get("positions", [])}
    history = list(account_state.get("history", []))

    t_plus_n = market_rules.get("t_plus_n", 1)
    commission_rules = market_rules.get("commission", {"rate": 0.0, "min_fee": 0.0})
    stamp_tax_rules = market_rules.get("stamp_tax", {"rate": 0.0, "direction": "sell"})
    daily_limit_cfg = market_rules.get("daily_limit", {})
    get_limit_pct = daily_limit_cfg.get("get_limit_pct", lambda sym: 0.10)

    current_date = timestamp.date()
    executed_trades: list[dict] = []
    rejected_orders: list[dict] = []
    pnl_delta = 0.0

    for order in orders:
        order_id = order.get("order_id", "")
        symbol = order.get("symbol", "")
        side = order.get("side", "buy")
        quantity = float(order.get("quantity", 0))
        order_type = order.get("order_type", "market")
        limit_price = order.get("price")

        bar = ohlcv.get(symbol)
        if bar is None:
            rejected_orders.append({**order, "reason": "no_market_data"})
            continue

        open_price = float(bar.get("open", 0))
        close_price = float(bar.get("close", 0))
        high_price = float(bar.get("high", open_price))
        low_price = float(bar.get("low", open_price))
        limit_pct = get_limit_pct(symbol)

        prev_close = float(bar.get("prev_close", close_price))

        if side == "buy":
            if prev_close > 0 and oprim.detect_daily_limit_up(
                close_price=close_price, prev_close=prev_close, limit_pct=limit_pct
            ):
                rejected_orders.append({**order, "reason": "limit_up_block_buy"})
                continue

        if side == "sell":
            if prev_close > 0 and oprim.detect_daily_limit_down(
                close_price=close_price, prev_close=prev_close, limit_pct=limit_pct
            ):
                rejected_orders.append({**order, "reason": "limit_down_block_sell"})
                continue

            if symbol in positions:
                pos = positions[symbol]
                entry_date = pos.get("entry_date")
                if entry_date is not None and oprim.t_plus_n_blocked(
                    entry_date=entry_date, current_date=current_date, t_plus_n=t_plus_n
                ):
                    rejected_orders.append({**order, "reason": f"t_plus_{t_plus_n}_blocked"})
                    continue

        if order_type == "market":
            fill_price = open_price
        elif order_type == "limit" and limit_price is not None:
            lp = float(limit_price)
            if side == "buy" and lp >= low_price:
                fill_price = min(lp, open_price)
            elif side == "sell" and lp <= high_price:
                fill_price = max(lp, open_price)
            else:
                rejected_orders.append({**order, "reason": "limit_not_hit"})
                continue
        else:
            fill_price = open_price

        trade_amount = fill_price * quantity
        fee = oprim.compute_commission(
            trade_amount=trade_amount,
            rate=commission_rules["rate"],
            min_fee=commission_rules.get("min_fee", 0.0),
        )
        tax_dir = stamp_tax_rules.get("direction", "sell")
        if (side == "buy" and tax_dir in ("buy", "both")) or (
            side == "sell" and tax_dir in ("sell", "both")
        ):
            tax = oprim.compute_stamp_tax(
                trade_amount=trade_amount,
                rate=stamp_tax_rules["rate"],
                direction=tax_dir,
            )
        else:
            tax = 0.0
        total_fees = fee + tax

        pnl_trade = 0.0
        if side == "buy":
            total_cost = trade_amount + total_fees
            if total_cost > cash:
                rejected_orders.append({**order, "reason": "insufficient_cash"})
                continue
            cash -= total_cost
            if symbol in positions:
                pos = positions[symbol]
                old_qty = pos["qty"]
                old_price = pos["entry_price"]
                new_qty = old_qty + quantity
                positions[symbol] = {
                    "symbol": symbol,
                    "qty": new_qty,
                    "entry_price": (old_qty * old_price + quantity * fill_price) / new_qty,
                    "entry_date": pos.get("entry_date", current_date),
                }
            else:
                positions[symbol] = {
                    "symbol": symbol,
                    "qty": quantity,
                    "entry_price": fill_price,
                    "entry_date": current_date,
                }
        else:
            if symbol not in positions:
                rejected_orders.append({**order, "reason": "no_position"})
                continue
            pos = positions[symbol]
            actual_qty = min(quantity, pos["qty"])
            proceeds = fill_price * actual_qty - total_fees
            pnl_trade = proceeds - pos["entry_price"] * actual_qty
            cash += proceeds
            pnl_delta += pnl_trade

            remaining_qty = pos["qty"] - actual_qty
            if remaining_qty <= 0:
                del positions[symbol]
            else:
                positions[symbol]["qty"] = remaining_qty

        trade_record = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "fill_price": fill_price,
            "fees": total_fees,
            "pnl": pnl_trade,
            "timestamp": timestamp.isoformat(),
        }
        executed_trades.append(trade_record)
        history.append(trade_record)

    new_account_state = {
        "cash": cash,
        "positions": list(positions.values()),
        "history": history,
    }

    return {
        "executed_trades": executed_trades,
        "rejected_orders": rejected_orders,
        "new_account_state": new_account_state,
        "pnl_delta": pnl_delta,
    }
