"""Normalize OKX WebSocket payloads to Nautilus event dicts."""
from __future__ import annotations


def okx_to_nautilus(payload: dict) -> dict:
    """Normalize an OKX WebSocket message to a Nautilus event dict.

    Supported channels: tickers, books, trades, candle* (candle1m, candle5m, candle1H, etc.)

    Parameters
    ----------
    payload : dict
        Raw OKX WebSocket push message with "arg" and "data" keys.

    Returns
    -------
    dict
        Nautilus-format event dict with keys:
        venue, instrument_id, event_type, timestamp_ns, data.

    Raises
    ------
    ValueError
        If the channel is not recognized.
    """
    arg = payload["arg"]
    channel = arg["channel"]
    inst_id = arg["instId"]
    data = payload["data"]

    instrument_id = f"{inst_id}.OKX"

    if channel == "tickers":
        d = data[0]
        ts_ns = int(d["ts"]) * 1_000_000
        return {
            "venue": "OKX",
            "instrument_id": instrument_id,
            "event_type": "tick",
            "timestamp_ns": ts_ns,
            "data": {
                "price": float(d["last"]),
                "bid": float(d["bidPx"]),
                "ask": float(d["askPx"]),
                "bid_size": float(d["bidSz"]),
                "ask_size": float(d["askSz"]),
                "volume_24h": float(d["vol24h"]),
            },
        }

    elif channel == "books":
        d = data[0]
        ts_ns = int(d["ts"]) * 1_000_000
        bids = [[float(b[0]), float(b[1])] for b in d["bids"]]
        asks = [[float(a[0]), float(a[1])] for a in d["asks"]]
        return {
            "venue": "OKX",
            "instrument_id": instrument_id,
            "event_type": "orderbook",
            "timestamp_ns": ts_ns,
            "data": {
                "bids": bids,
                "asks": asks,
            },
        }

    elif channel == "trades":
        d = data[0]
        ts_ns = int(d["ts"]) * 1_000_000
        return {
            "venue": "OKX",
            "instrument_id": instrument_id,
            "event_type": "trade",
            "timestamp_ns": ts_ns,
            "data": {
                "price": float(d["px"]),
                "size": float(d["sz"]),
                "side": d["side"],
                "trade_id": str(d["tradeId"]),
            },
        }

    elif channel.startswith("candle"):
        d = data[0]
        ts_ns = int(d[0]) * 1_000_000
        return {
            "venue": "OKX",
            "instrument_id": instrument_id,
            "event_type": "bar",
            "timestamp_ns": ts_ns,
            "data": {
                "open": float(d[1]),
                "high": float(d[2]),
                "low": float(d[3]),
                "close": float(d[4]),
                "volume": float(d[5]),
                "bar_type": channel,
            },
        }

    else:
        raise ValueError(
            f"Unrecognized OKX channel: {channel!r}. "
            "Supported: tickers, books, trades, candle*"
        )
