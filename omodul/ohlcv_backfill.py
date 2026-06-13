"""omodul.ohlcv_backfill — Fetch and persist OHLCV history idempotently.

Pillars: decision_trail
Composites: oprim.ohlcv_fetch + obase.persistence.write_one
"""
from __future__ import annotations

from typing import Any, ClassVar

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


class OhlcvBackfillConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "ohlcv_backfill"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}
    _fingerprint_fields: ClassVar[set[str]] = {"symbol", "interval", "limit"}

    symbol: str
    interval: str = "1H"
    limit: int = 100
    venue: str = "okx"
    table: str = "ohlcv"


async def ohlcv_backfill(
    *,
    config: OhlcvBackfillConfig,
    pool: Any = None,
) -> dict[str, Any]:
    """Fetch OHLCV bars and write them idempotently to the persistence store.

    Uses ON CONFLICT DO NOTHING so re-running the same backfill is safe.
    The decision_trail records how many rows were written vs already present.

    Args:
        config: OhlcvBackfillConfig with symbol, interval, limit, venue.
        pool: asyncpg connection pool from obase.persistence.  When None
            the write step is skipped (dry-run / test mode).

    Returns:
        Standard omodul result dict with keys:
        ``bars_fetched``, ``bars_written``, ``symbol``, ``interval``.
    """
    from oprim.ohlcv_fetch import ohlcv_fetch  # noqa: PLC0415

    trail = Trail()
    trail.record("fetch_start", {"symbol": config.symbol, "interval": config.interval,
                                  "limit": config.limit, "venue": config.venue})

    bars = await ohlcv_fetch(
        config.symbol,
        venue=config.venue,
        interval=config.interval,
        limit=config.limit,
    )

    trail.record("fetch_complete", {"bars_fetched": len(bars)})

    bars_written = 0
    if pool is not None:
        from obase.persistence.crud import write_one  # noqa: PLC0415

        for bar in bars:
            row = {
                "instrument": config.symbol,
                "bar": config.interval,
                "ts": bar["ts"],
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "vol": bar["vol"],
            }
            await write_one(
                pool,
                table=config.table,
                data=row,
                conflict_on=["instrument", "bar", "ts"],
                # update_columns=None → ON CONFLICT DO NOTHING (idempotent)
            )
            bars_written += 1

    trail.record("write_complete", {"bars_written": bars_written})

    return build_result(
        config=config,
        trail=trail,
        payload={
            "bars_fetched": len(bars),
            "bars_written": bars_written,
            "symbol": config.symbol,
            "interval": config.interval,
        },
    )
