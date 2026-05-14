"""Universe selection specifications."""
from __future__ import annotations

VALID_INSTRUMENT_TYPES = {"spot", "perpetual", "futures", "option", "index_future"}


def fixed_list(
    symbols: list[str],
    venue: str,
    instrument_type: str,
    market_metadata: dict | None = None,
) -> dict:
    """Static universe specification.

    Parameters
    ----------
    symbols : list[str]
        List of symbol identifiers (e.g. ["BTC-USDT", "ETH-USDT"]).
    venue : str
        Exchange venue name (e.g. "OKX").
    instrument_type : str
        One of VALID_INSTRUMENT_TYPES.
    market_metadata : dict | None
        Optional metadata about market conditions or instrument properties.

    Returns
    -------
    dict
        Universe spec with keys: venue, instrument_type, symbols,
        instrument_ids, metadata.

    Raises
    ------
    ValueError
        If symbols is empty or instrument_type is invalid.
    """
    if not symbols:
        raise ValueError("symbols must be non-empty")

    if instrument_type not in VALID_INSTRUMENT_TYPES:
        raise ValueError(
            f"instrument_type must be one of {sorted(VALID_INSTRUMENT_TYPES)}, "
            f"got {instrument_type!r}"
        )

    instrument_ids = [f"{sym}.{venue}" for sym in symbols]

    return {
        "venue": venue,
        "instrument_type": instrument_type,
        "symbols": list(symbols),
        "instrument_ids": instrument_ids,
        "metadata": dict(market_metadata) if market_metadata else {},
    }
