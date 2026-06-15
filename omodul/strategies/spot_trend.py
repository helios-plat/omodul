"""Strategy: spot_trend — daily spot Donchian trend, long-only, bear filter.

Config-driven: uses trend_signal_compose with long-only direction.
Bear filter: skip entries when close < 200-bar SMA (configurable).
"""

from __future__ import annotations

import numpy as np

try:
    from oprim.crypto import sha256_hash
    from oprim.serialization import canonical_json
except ImportError:
    def sha256_hash(x): return b""  # type: ignore
    def canonical_json(x): return str(x)  # type: ignore

try:
    from oprim.technical import sma, donchian_channel
except ImportError:
    sma = donchian_channel = None  # type: ignore

try:
    from oskill.trend_compose import trend_signal_compose
except ImportError:
    trend_signal_compose = None  # type: ignore


def _args_hash(obj) -> str:
    result = sha256_hash(canonical_json({"v": str(obj)}))
    hex_str = result.hex() if isinstance(result, bytes) else result
    return hex_str[:16]


def spot_trend(market_state: dict, config: dict) -> dict:
    """Daily spot trend strategy: long-only Donchian with 200d MA bear filter.

    Signal logic:
      1. Base signal from trend_signal_compose (direction forced to 'long')
      2. Bear filter: zero out entries (but keep exits) when close < SMA(bear_ma)
      3. Donchian exit when close < rolling N_exit low

    Parameters
    ----------
    market_state : dict
        Keys: 'ohlcv' (dict with 'high','low','close','volume'),
              'instrument', 'current_positions', 'capital_usd'.
    config : dict
        YAML-loaded strategy config. Keys:
          indicators: {supertrend, ema, adx, macd} or donchian-specific overrides
          signal_logic: {min_confluence, direction} — direction overridden to 'long'
          risk: {cost_bps, bear_ma (default 200)}
          donchian: {n_enter (20), n_exit (10)}   # standalone mode

    Returns
    -------
    dict
        signals: np.ndarray of int8 (+1/0 only — long-only)
        n_signals: count of non-zero entries
        cost_bps: from config
        audit_evidence: {stack_calls, config_fingerprint, n_bars, bear_filter_applied}
    """
    ohlcv    = market_state["ohlcv"]
    closes   = np.asarray(ohlcv["close"], dtype=float)
    highs    = np.asarray(ohlcv.get("high", closes), dtype=float)
    lows     = np.asarray(ohlcv.get("low",  closes), dtype=float)
    n_bars   = len(closes)
    risk_cfg = config.get("risk", {})
    cost_bps = float(risk_cfg.get("cost_bps", 10.0))
    bear_ma  = int(risk_cfg.get("bear_ma", 200))

    stack_calls  = []
    cfg_fp       = _args_hash(config)

    # ── Choose signal source ──
    donchian_cfg = config.get("donchian", {})
    if donchian_cfg:
        # Standalone Donchian mode: breakout on closes, shifted 1 bar (no lookahead).
        # Entry: close[i] > max(close[i-n_enter:i])   (prior n_enter bars, shift=1)
        # Exit:  close[i] < min(close[i-n_exit:i])
        import pandas as pd

        n_enter = int(donchian_cfg.get("n_enter", 20))
        n_exit  = int(donchian_cfg.get("n_exit",  10))

        c_series    = pd.Series(closes)
        upper_enter = c_series.rolling(n_enter).max().shift(1).to_numpy()
        lower_exit  = c_series.rolling(n_exit).min().shift(1).to_numpy()

        signals  = np.zeros(n_bars, dtype=np.int8)
        position = 0
        for i in range(n_bars):
            if np.isnan(upper_enter[i]) or np.isnan(lower_exit[i]):
                continue
            if position == 0 and closes[i] > upper_enter[i]:
                signals[i] = 1
                position = 1
            elif position == 1 and closes[i] < lower_exit[i]:
                signals[i] = -1  # exit → flatten
                position = 0

        stack_calls.append({
            "function": "donchian_close_breakout (shift=1)",
            "n_enter": n_enter, "n_exit": n_exit,
        })
    else:
        # Compose mode: override direction to long-only
        if trend_signal_compose is None:
            raise ImportError("oskill.trend_compose is required")
        cfg_override = dict(config)
        cfg_override["signal_logic"] = dict(config.get("signal_logic", {}))
        cfg_override["signal_logic"]["direction"] = "long"
        signals = trend_signal_compose(ohlcv, config=cfg_override)
        stack_calls.append({
            "function": "oskill.trend_compose.trend_signal_compose (long-only)",
            "config_fingerprint": cfg_fp,
        })

    # ── Bear filter: suppress new entries (signal==1) when close < SMA(bear_ma) ──
    bear_applied = 0
    if sma is not None and bear_ma > 0 and n_bars >= bear_ma:
        ma_series = np.asarray(sma(closes, window=bear_ma), dtype=float)
        for i in range(n_bars):
            if signals[i] == 1 and not np.isnan(ma_series[i]) and closes[i] < ma_series[i]:
                signals[i] = 0
                bear_applied += 1
        stack_calls.append({
            "function": "oprim.technical.sma (bear filter)",
            "bear_ma": bear_ma, "entries_suppressed": bear_applied,
        })

    return {
        "signals": signals,
        "n_signals": int(np.sum(signals != 0)),
        "cost_bps": cost_bps,
        "audit_evidence": {
            "stack_calls": stack_calls,
            "config_fingerprint": cfg_fp,
            "n_bars": n_bars,
            "bear_filter_applied": bear_applied,
        },
    }
