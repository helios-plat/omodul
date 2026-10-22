"""Readonly broker portfolio synchronization transaction (omodul).
Gloomberb (https://github.com/gloom-sh/gloomberb, MIT) was reviewed as the
requested reference; no Gloomberb source code is copied here.
"""
from __future__ import annotations

import asyncio
import math
from typing import Any

from obase.brokers.okx_readonly import OKXReadonlyBrokerAdapter


async def sync_readonly_portfolio(
    api_key: str | None = None,
    api_secret: str | None = None,
    passphrase: str | None = None,
) -> dict[str, Any]:
    """Read account and position snapshots with explicit degraded status."""
    adapter = OKXReadonlyBrokerAdapter(
        api_key=api_key, api_secret=api_secret, passphrase=passphrase
    )
    balance_result, positions_result = await asyncio.gather(
        adapter.get_balance(),
        adapter.get_positions(),
    )
    balance_ok = balance_result.get("status") == "success"
    positions_ok = positions_result.get("status") == "success"
    positions = positions_result.get("positions", []) if positions_ok else []
    balance = balance_result.get("balance", []) if balance_ok else []

    notionals: list[float] = []
    complete_notional_data = positions_ok
    for position in positions:
        try:
            value = float(position["notionalUsd"])
        except (KeyError, TypeError, ValueError):
            complete_notional_data = False
            break
        if not math.isfinite(value):
            complete_notional_data = False
            break
        notionals.append(value)
    total_notional = sum(notionals) if complete_notional_data else None
    degraded_reasons = []
    if not balance_ok:
        degraded_reasons.append(f"balance:{balance_result['reason']}")
    if not positions_ok:
        degraded_reasons.append(f"positions:{positions_result['reason']}")

    return {
        "sync_status": "success" if balance_ok and positions_ok else "degraded",
        "degraded_reason": "; ".join(degraded_reasons) or None,
        "balance": balance,
        "position_count": len(positions) if positions_ok else None,
        "total_notional_usd": total_notional,
        "broker": "OKX (Readonly)"
    }
