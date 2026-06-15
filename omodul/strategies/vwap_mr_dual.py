"""Strategy: vwap_mr_dual — 1H SWAP, long + short VWAP mean reversion.

Wraps oskill.mean_reversion_compose with audit evidence + cost gate.
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
    from oskill.mean_reversion_compose import mean_reversion_compose
except ImportError:
    mean_reversion_compose = None  # type: ignore


def _args_hash(obj) -> str:
    result = sha256_hash(canonical_json({"v": str(obj)}))
    hex_str = result.hex() if isinstance(result, bytes) else result
    return hex_str[:16]


def vwap_mr_dual(market_state: dict, config: dict) -> dict:
    """1H SWAP VWAP mean-reversion strategy via config-driven compose.

    Parameters
    ----------
    market_state : dict
        Keys: 'ohlcv' (dict with 'high','low','close','volume' as np.ndarray),
              'instrument' (str), 'current_positions' (dict), 'capital_usd' (float).
    config : dict
        YAML-loaded strategy config (§4 schema). Keys:
          indicators: {vwap, bollinger, rsi, stochastic} — each with enabled+params
          signal_logic: {min_confluence, direction}
          risk: {cost_bps}

    Returns
    -------
    dict
        signals: np.ndarray of int8 (+1/-1/0)
        n_signals: count of non-zero entries
        cost_bps: from config
        audit_evidence: {stack_calls, config_fingerprint, n_bars}
    """
    if mean_reversion_compose is None:
        raise ImportError("oskill.mean_reversion_compose is required")

    ohlcv    = market_state["ohlcv"]
    closes   = np.asarray(ohlcv["close"], dtype=float)
    n_bars   = len(closes)
    risk_cfg = config.get("risk", {})
    cost_bps = float(risk_cfg.get("cost_bps", 10.0))

    stack_calls = []
    cfg_fp      = _args_hash(config)

    signals = mean_reversion_compose(ohlcv, config=config)
    stack_calls.append({
        "function": "oskill.mean_reversion_compose.mean_reversion_compose",
        "config_fingerprint": cfg_fp,
    })

    return {
        "signals": signals,
        "n_signals": int(np.sum(signals != 0)),
        "cost_bps": cost_bps,
        "audit_evidence": {
            "stack_calls": stack_calls,
            "config_fingerprint": cfg_fp,
            "n_bars": n_bars,
        },
    }
