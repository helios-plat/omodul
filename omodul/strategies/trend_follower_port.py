"""Strategy: trend_follower_port — backtest signal for the ported helixa trend_follower.

Reconstructs the live paper strategy's per-bar position series (+1 long / -1 short / 0
flat) so tools/strategy_gate.py can DSR/PBO-gate it. Mirrors the live logic in
helivex paper/strategies/trend_follower_port.py:
  entry: close breaks Donchian(20) AND ADX(14) >= adx_entry AND ATR/close in health band
  exit:  Chandelier(HH/LL(22) ∓ ATR*mult) stop OR ADX < adx_exit OR held >= max_holding
"""

from __future__ import annotations

import numpy as np


def _atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    n = len(close)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = np.full(n, np.nan)
    if n >= period + 1:
        atr[period] = tr[1 : period + 1].mean()
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _adx_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    n = len(close)
    pdm = np.zeros(n)
    mdm = np.zeros(n)
    tr = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i - 1]
        dn = low[i - 1] - low[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))

    def _wilder(x: np.ndarray) -> np.ndarray:
        out = np.full(n, np.nan)
        if n < period + 1:
            return out
        s = x[1 : period + 1].sum()
        out[period] = s
        for i in range(period + 1, n):
            s = s - (s / period) + x[i]
            out[i] = s
        return out

    atr_s, pdm_s, mdm_s = _wilder(tr), _wilder(pdm), _wilder(mdm)
    dx = np.full(n, np.nan)
    for i in range(period, n):
        a = atr_s[i]
        if not np.isfinite(a) or a == 0:
            continue
        pdi = 100.0 * pdm_s[i] / a
        mdi = 100.0 * mdm_s[i] / a
        denom = pdi + mdi
        if denom == 0:
            continue
        dx[i] = 100.0 * abs(pdi - mdi) / denom
    adx = np.full(n, np.nan)
    valid = np.where(np.isfinite(dx))[0]
    if len(valid) >= period:
        start = valid[period - 1]
        adx[start] = np.nanmean(dx[valid[:period]])
        for i in range(start + 1, n):
            if np.isfinite(dx[i]) and np.isfinite(adx[i - 1]):
                adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
            else:
                adx[i] = adx[i - 1]
    return adx


def trend_follower_port(market_state: dict, config: dict) -> dict:
    ohlcv = market_state["ohlcv"]
    high = np.asarray(ohlcv["high"], dtype=float)
    low = np.asarray(ohlcv["low"], dtype=float)
    close = np.asarray(ohlcv["close"], dtype=float)
    n = len(close)

    live = config.get("live", {})
    dp = int(live.get("donchian_period", 20))
    ap = int(live.get("adx_period", 14))
    adx_entry = float(live.get("adx_entry", 20.0))
    adx_exit = float(live.get("adx_exit", 15.0))
    cp = int(live.get("chandelier_period", 22))
    cm = float(live.get("chandelier_mult", 3.0))
    max_hold = int(live.get("max_holding_days", 30))
    atr_ind = config.get("indicators", {}).get("atr", {})
    h_min = float(atr_ind.get("health_min", 0.005))
    h_max = float(atr_ind.get("health_max", 0.10))
    cost_bps = float(config.get("risk", {}).get("cost_bps", 10.0))

    atr = _atr_series(high, low, close, ap)
    adx = _adx_series(high, low, close, ap)

    signals = np.zeros(n, dtype=np.int8)
    pos = 0
    held = 0
    need = max(dp, cp, 2 * ap) + 1
    for i in range(need, n):
        a = adx[i]
        t = atr[i]
        if not (np.isfinite(a) and np.isfinite(t)):
            signals[i] = pos
            continue
        c = close[i]
        don_hi = high[i - dp : i].max()
        don_lo = low[i - dp : i].min()
        hh = high[i - cp : i].max()
        ll = low[i - cp : i].min()
        chand_long = hh - t * cm
        chand_short = ll + t * cm
        health = h_min <= (t / c if c else 0.0) <= h_max
        if pos == 0:
            if c > don_hi and a >= adx_entry and health:
                pos, held = 1, 0
            elif c < don_lo and a >= adx_entry and health:
                pos, held = -1, 0
        elif pos == 1:
            held += 1
            if c < chand_long or a < adx_exit or held >= max_hold:
                pos = 0
        elif pos == -1:
            held += 1
            if c > chand_short or a < adx_exit or held >= max_hold:
                pos = 0
        signals[i] = pos

    return {
        "signals": signals,
        "n_signals": int(np.sum(signals != 0)),
        "cost_bps": cost_bps,
        "audit_evidence": {"stack_calls": [{"function": "trend_follower_port"}], "n_bars": n},
    }
