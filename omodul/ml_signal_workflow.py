"""ML directional signal engine — LightGBM + triple-barrier + walk-forward + DSR gate.

4-pillar omodul. Orchestrates:
    1. oskill.signal.ml_feature_matrix   — alpha-style features.
    2. oprim.triple_barrier_label        — supervised labels.
    3. LightGBM walk-forward validation  — OOS predictions (a stage function).
    4. oprim.deflated_sharpe             — the REAL promotion gate.

Extraction source: helixa services/qlib-v2. The decisive difference from helixa:
helixa's DSR/walk-forward gate ran with VALIDATION_STRICT=false and NEVER fired
in production, so a directionally-wrong model (live accuracy 0.2315) kept voting.
Here the gate is load-bearing: `findings["promoted"]` is False unless the OOS
Deflated Sharpe — computed on returns NET of taker fees per position change —
clears the threshold, and un-promoted signals are observe-only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import numpy as np

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint


class MlSignalConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "ml_signal_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {
        "symbol",
        "take_profit_pct",
        "stop_loss_pct",
        "horizon",
    }

    symbol: str = "BTC-USDT-SWAP"
    take_profit_pct: float = 0.015
    stop_loss_pct: float = 0.010
    horizon: int = 30
    wfv_train: int = 500
    wfv_test: int = 100
    wfv_step: int = 100
    dsr_threshold: float = 0.60
    dsr_n_trials: int = 10  # conservative multiple-testing deflation
    # Taker fee applied to every OOS position change before the DSR gate. A
    # frictionless gate promoted a model whose 5m-frequency gross alpha was
    # ~4.6x smaller than its fee bill (2026-07-23 full-factor probe: ETH gross
    # +2.35% vs fees 10.8%) — the gate must price the turnover it implies.
    taker_fee_bps: float = 5.0
    # Sharpe annualization factor for `closes`' actual bar frequency. Default is
    # 5m bars on a 24/7 crypto market (365*24*12=105,120 bars/yr — same convention
    # as ops/scripts/scalping_gate_r51.py's PERIODS_5M), NOT the equity-market
    # sqrt(252) that was hardcoded here before: that silently assumed 1
    # observation = 1 trading day while every caller actually feeds 5m closes,
    # understating oos_sharpe (and therefore the DSR gate) by ~20x.
    bars_per_year: int = 105_120


def _fit_predict_lgb(x_train, y_train, x_test):
    import lightgbm as lgb

    classes = sorted(set(y_train.tolist()))
    if len(classes) < 2:
        return np.zeros(len(x_test), dtype=int)  # degenerate fold → all neutral
    remap = {c: i for i, c in enumerate(classes)}
    inv = {i: c for c, i in remap.items()}
    y_enc = np.array([remap[v] for v in y_train])
    # let LGBMClassifier infer binary vs multiclass from the label count (a
    # forced objective="multiclass" fatals when a fold has only 2 classes)
    model = lgb.LGBMClassifier(
        num_leaves=31,
        learning_rate=0.05,
        n_estimators=120,
        feature_fraction=0.8,
        bagging_fraction=0.8,
        bagging_freq=5,
        n_jobs=2,
        verbose=-1,
    )
    model.fit(x_train, y_enc)
    pred_enc = model.predict(x_test)
    return np.array([inv[int(p)] for p in pred_enc])


def ml_signal_workflow(
    config: MlSignalConfig,
    input_data: dict,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """Train a walk-forward LightGBM directional model on ``input_data['closes']``,
    gate it on out-of-sample Deflated Sharpe, and emit the latest signal.

    findings: ``{direction, score, confidence, wfv_accuracy, oos_sharpe, dsr,
    promoted, n_folds, n_trials}``. `promoted=False` (default outcome on thin /
    non-predictive data) means the signal must be treated as observe-only.
    """
    from obase.cost_tracker import CostTracker

    started_at = datetime.now(UTC)
    trail_steps: list[dict] = []
    cost_tracker = CostTracker()
    fingerprint = compute_fingerprint(config, {"symbol": config.symbol})

    def _rec(layer, name, out, t0):
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer=layer,
            callable_name=name,
            inputs_summary={"symbol": config.symbol},
            outputs_summary=out,
            started_at=t0,
        )

    try:
        from oprim.deflated_sharpe import deflated_sharpe
        from oprim.technical.triple_barrier_label import triple_barrier_label
        from oskill.signal.ml_feature_matrix import ml_feature_matrix

        closes = np.asarray(input_data["closes"], dtype=float)
        # Full-OHLCV mode: when the caller supplies all four extra arrays the
        # feature matrix carries the complete helixa Alpha158 factor set
        # (volume/VWAP/Bollinger/candle/breaks/ATR/efficiency); closes-only
        # callers keep the compact price-derived set.
        ohlcv_keys = ("opens", "highs", "lows", "volumes")
        extra = {
            k: np.asarray(input_data[k], dtype=float)
            for k in ohlcv_keys
            if input_data.get(k) is not None
        }

        t0 = datetime.now(UTC)
        feats = ml_feature_matrix(closes, **extra)
        n_features = feats.shape[1] - 1
        _rec(
            "oskill",
            "ml_feature_matrix",
            {"rows": len(feats), "cols": n_features, "full_ohlcv": len(extra) == 4},
            t0,
        )

        t0 = datetime.now(UTC)
        labels_full = triple_barrier_label(
            closes,
            take_profit_pct=config.take_profit_pct,
            stop_loss_pct=config.stop_loss_pct,
            horizon=config.horizon,
        )
        bar_idx = feats["_bar_index"].to_numpy()
        # Triple-barrier labels for the last `horizon` bars in the series are
        # unlabelable (not enough forward data to resolve a barrier) and come
        # back as 0 — same value as a genuine "price stayed inside both
        # barriers" neutral. Left in, they get trained on as real neutrals.
        # Drop them here rather than silently mixing the two.
        labelable = bar_idx < (len(closes) - config.horizon)
        bar_idx = bar_idx[labelable]
        y = labels_full[bar_idx]
        x = feats.drop(columns=["_bar_index"]).to_numpy()[labelable]
        _rec("oprim", "triple_barrier_label", {"n_labeled": int((y != 0).sum())}, t0)

        # walk-forward OOS predictions
        t0 = datetime.now(UTC)
        n = len(x)
        preds_all: list[int] = []
        actual_ret: list[float] = []
        correct = 0
        counted = 0
        n_folds = 0
        start = 0
        while start + config.wfv_train + config.wfv_test <= n:
            # Purge: a training-window label at position i is built from
            # closes[i+1 : i+1+horizon], so the last `horizon` rows of the
            # training slice have labels resolved using price bars that fall
            # inside the adjacent test window — the model would train on
            # outcomes it's about to be scored on. Drop those rows from the
            # training slice (a plain purge; test set is untouched).
            tr = slice(start, max(start, start + config.wfv_train - config.horizon))
            te = slice(start + config.wfv_train, start + config.wfv_train + config.wfv_test)
            pred = _fit_predict_lgb(x[tr], y[tr], x[te])
            # OOS strategy return = predicted direction * next-bar return at that bar
            te_bars = bar_idx[te]
            for k, b in enumerate(te_bars):
                if b + 1 >= len(closes):
                    continue
                fwd = closes[b + 1] / closes[b] - 1.0
                p = int(pred[k])
                preds_all.append(p)
                actual_ret.append(p * fwd)
                if p != 0:
                    counted += 1
                    if (p > 0 and fwd > 0) or (p < 0 and fwd < 0):
                        correct += 1
            n_folds += 1
            start += config.wfv_step

        wfv_accuracy = (correct / counted) if counted else 0.0
        strat = np.asarray(actual_ret, dtype=float)
        # Net returns: charge the taker fee on every position change the OOS
        # prediction sequence implies (folds are contiguous, so the sequence is
        # a coherent position path). The gate runs on NET returns — gross is
        # kept in findings for observability only.
        pos_path = np.asarray(preds_all, dtype=float)
        turns = np.abs(np.diff(np.concatenate([[0.0], pos_path])))
        strat_net = strat - turns * (config.taker_fee_bps / 10_000.0)

        def _sharpe(r: np.ndarray) -> float:
            if len(r) > 5 and r.std(ddof=1) > 0:
                return float(r.mean() / r.std(ddof=1) * np.sqrt(config.bars_per_year))
            return 0.0

        sharpe = _sharpe(strat)
        sharpe_net = _sharpe(strat_net)
        dsr_res = deflated_sharpe(sharpe_net, config.dsr_n_trials, returns=strat_net.tolist())
        # dsr_probability ∈ [0,1] is the DSR in helixa/factor-analyzer's ≥0.60 convention;
        # deflated_sharpe is the (possibly negative) deflated SR magnitude.
        dsr_val = float(dsr_res["dsr_probability"])
        deflated_sr = float(dsr_res["deflated_sharpe"])
        _rec(
            "oprim",
            "deflated_sharpe",
            {
                "wfv_accuracy": round(wfv_accuracy, 4),
                "oos_sharpe": round(sharpe, 3),
                "oos_sharpe_net": round(sharpe_net, 3),
                "dsr": round(dsr_val, 4),
            },
            t0,
        )

        promoted = dsr_val > config.dsr_threshold and wfv_accuracy > 0.5

        # latest signal: train on all, predict last row
        last_pred = (
            int(_fit_predict_lgb(x[:-1], y[:-1], x[-1:].reshape(1, -1))[0])
            if n > config.wfv_train
            else 0
        )
        direction = "long" if last_pred > 0 else ("short" if last_pred < 0 else "neutral")

        findings = {
            "direction": direction,
            "score": float(last_pred),
            "confidence": abs(float(last_pred)) * min(1.0, max(0.0, dsr_val)),
            "wfv_accuracy": wfv_accuracy,
            "oos_sharpe": sharpe,
            "oos_sharpe_net": sharpe_net,
            "fee_drag_pct": round(
                float((turns * (config.taker_fee_bps / 10_000.0)).sum()) * 100, 3
            ),
            "dsr": dsr_val,
            "deflated_sharpe": deflated_sr,
            "dsr_threshold": config.dsr_threshold,
            "promoted": promoted,
            "n_folds": n_folds,
            "n_trials": config.dsr_n_trials,
            "n_features": n_features,
            "full_ohlcv": len(extra) == 4,
            "note": "promoted=False -> observe-only, not a live vote (helixa never gated this)",
        }

        report_path = None
        if "report" in config._enabled_pillars:
            report_path = output_dir / f"{config._omodul_name}_{fingerprint[:8]}.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                f"# ml_signal_workflow ({config.symbol})\n\n{json.dumps(findings, default=str, indent=2)}\n"
            )
        trail = build_decision_trail(
            fingerprint=fingerprint,
            config=config,
            input_data={"symbol": config.symbol},
            trail_steps=trail_steps,
            cost_tracker=cost_tracker,
            started_at=started_at,
            status="completed",
            error=None,
        )
        return {
            "findings": findings,
            "status": "completed",
            "error": None,
            "fingerprint": fingerprint,
            "decision_trail": trail,
            "report_path": report_path,
            "cost_usd": cost_tracker.total_usd,
        }
    except Exception as exc:
        trail = build_decision_trail(
            fingerprint=fingerprint,
            config=config,
            input_data={"symbol": config.symbol},
            trail_steps=trail_steps,
            cost_tracker=cost_tracker,
            started_at=started_at,
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        return {
            "findings": None,
            "status": "failed",
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "fingerprint": fingerprint,
            "decision_trail": trail,
            "report_path": None,
            "cost_usd": cost_tracker.total_usd,
        }
