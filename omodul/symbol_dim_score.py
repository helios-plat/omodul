"""omodul.symbol_dim_score — 8 维度综合评分.

IO 剥离: 服务层负责所有数据预取, 通过 input_data 注入.
并行化: 8 个 dim 用 ThreadPoolExecutor 并行计算.
Pillars: fingerprint + decision_trail (no cost, no report).

DEVIATION §2.3: ThreadPoolExecutor without manual copy_context() wrapping.
Python 3.12+ ThreadPoolExecutor auto-propagates contextvars. Cost pillar not
enabled; cost_tracker ContextVar unused. Awaiting Owner confirmation.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Set

from obase.cost_tracker import CostTracker
from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint as _compute_fp_generic
from oprim._exceptions import OprimError
from oprim.apply_screen_filter import ScreenRule, apply_screen_filter
from oprim.beneish_m_score import BeneishInput, beneish_m_score
from oprim.dcf_valuation import dcf_valuation
from oprim.dupont_decomposition import dupont_decomposition
from oprim.financial_metric_extraction import NewsItem, financial_metric_extraction
from oprim.kdj import kdj
from oprim.limit_status_calc import limit_status_calc
from oprim.pattern_detection import OHLCVInput, pattern_detection
from oprim.policy_event_extraction import PolicyNews, policy_event_extraction
from oprim.volume_ratio import volume_ratio
from pydantic import BaseModel, Field, field_validator

_VERSION = "1.0.0"
_FALLBACK_SCORE = 50.0
_INSUFFICIENT = "insufficient_data"
_SEVERITY_WEIGHT = {"minor": 0.5, "moderate": 1.0, "major": 1.5, "critical": 2.0}


def compute_fingerprint_for(config: "SymbolDimScoreConfig", input_data: Any) -> str:
    """公开 fingerprint API. 只依赖 {symbol, trade_date} — 不含 input_data."""
    raw = f"{config.symbol}|{config.trade_date.isoformat()}|{_VERSION}"
    return hashlib.sha256(raw.encode()).hexdigest()


class SymbolDimScoreConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "symbol_dim_score"
    _omodul_version: ClassVar[str] = _VERSION
    _enabled_pillars: ClassVar[Set[str]] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[Set[str]] = {"symbol", "trade_date"}

    symbol: str
    trade_date: "date"

    @field_validator("symbol")
    @classmethod
    def _check_symbol(cls, v: str) -> str:
        if not v:
            raise ValueError("symbol must be non-empty")
        return v


class SymbolDimScoreInput(BaseModel):
    ohlcv: dict[str, list[float]] = Field(default_factory=dict)
    financials: dict[str, Any] | None = None
    valuation: dict[str, Any] | None = None
    news: list[dict[str, Any]] | None = None
    market_distribution: dict[str, Any] = Field(default_factory=dict)


class SymbolDimScoreFindings(BaseModel):
    scores: dict[str, float]
    evidence: dict[str, dict[str, Any]]


# ── dim scorers ────────────────────────────────────────────────────────────────


def _compute_technical(inp: SymbolDimScoreInput) -> tuple[float, dict[str, Any]]:
    ohlcv = inp.ohlcv
    if not all(k in ohlcv and len(ohlcv[k]) >= 9 for k in ("high", "low", "close", "volume")):
        raise ValueError(_INSUFFICIENT)
    kdj_res = kdj(high=ohlcv["high"], low=ohlcv["low"], close=ohlcv["close"])
    j_last = kdj_res.j[-1]
    kdj_score = float(max(0.0, min(100.0, 100.0 - j_last)))
    limit_res = limit_status_calc(close=ohlcv["close"], lookback=min(5, len(ohlcv["close"]) - 1))
    limit_up_count = limit_res.recent.count("limit_up")
    limit_score = min(100.0, 50.0 + limit_up_count * 10.0)
    vr = volume_ratio(volumes=ohlcv["volume"])
    vol_score = float(min(100.0, 40.0 + vr * 10.0))
    score = kdj_score * 0.4 + limit_score * 0.3 + vol_score * 0.3
    return score, {"kdj_j": j_last, "limit_up_count": limit_up_count, "volume_ratio": vr}


def _compute_fundamentals(inp: SymbolDimScoreInput) -> tuple[float, dict[str, Any]]:
    fin = inp.financials
    if not fin:
        raise ValueError(_INSUFFICIENT)
    required = ("net_profit", "revenue", "total_assets", "total_liabilities", "operating_cash_flow")
    if not all(k in fin for k in required):
        raise ValueError(_INSUFFICIENT)
    curr = BeneishInput(**{k: float(fin[k]) for k in required})
    prior_data = fin.get("prior", fin)
    prior = BeneishInput(**{k: float(prior_data[k]) for k in required})
    m_res = beneish_m_score(current=curr, prior=prior)
    fraud_penalty = max(0.0, (m_res.m_score + 2.22) * 10.0)
    beneish_score = float(max(0.0, min(100.0, 70.0 - fraud_penalty)))
    try:
        dp = dupont_decomposition(
            net_income=float(fin["net_profit"]),
            revenue=float(fin["revenue"]),
            total_assets=float(fin["total_assets"]),
            total_equity=float(
                fin.get("total_equity", fin["total_assets"] - fin["total_liabilities"])
            ),
        )
        roe_score = float(min(100.0, max(0.0, 50.0 + dp.roe * 200.0)))
        roe_val = dp.roe
    except Exception:
        roe_score = _FALLBACK_SCORE
        roe_val = None
    return beneish_score * 0.5 + roe_score * 0.5, {"beneish_m": m_res.m_score, "roe": roe_val}


def _compute_valuation(inp: SymbolDimScoreInput) -> tuple[float, dict[str, Any]]:
    val, fin = inp.valuation, inp.financials
    if not val or not fin:
        raise ValueError(_INSUFFICIENT)
    current_price = float(val.get("current_price", 0))
    shares = float(val.get("shares_outstanding", 0))
    if current_price <= 0 or shares <= 0:
        raise ValueError(_INSUFFICIENT)
    fcfs = val.get("free_cash_flows")
    if not fcfs:
        raise ValueError(_INSUFFICIENT)
    dcf = dcf_valuation(free_cash_flows=[float(f) for f in fcfs], shares_outstanding=shares)
    margin = (dcf.intrinsic_value_per_share - current_price) / (
        dcf.intrinsic_value_per_share + 1e-12
    )
    return float(max(0.0, min(100.0, 50.0 + margin * 100.0))), {
        "intrinsic": dcf.intrinsic_value_per_share,
        "current": current_price,
        "margin": margin,
    }


def _compute_sentiment(inp: SymbolDimScoreInput) -> tuple[float, dict[str, Any]]:
    if not inp.news:
        raise ValueError(_INSUFFICIENT)
    items = [NewsItem(content=str(n.get("content", "")), source=n.get("source")) for n in inp.news]
    metrics = financial_metric_extraction(news=items)
    if not metrics:
        return _FALLBACK_SCORE, {"metrics_found": 0, "avg_sentiment": 0.0}
    avg = sum(m.sentiment_score for m in metrics) / len(metrics)
    return float(max(0.0, min(100.0, 50.0 + avg * 50.0))), {
        "metrics_found": len(metrics),
        "avg_sentiment": avg,
    }


def _compute_risk(inp: SymbolDimScoreInput) -> tuple[float, dict[str, Any]]:
    import numpy as np

    if not inp.financials or "close" not in inp.ohlcv or len(inp.ohlcv["close"]) < 5:
        raise ValueError(_INSUFFICIENT)
    closes = np.asarray(inp.ohlcv["close"], dtype=np.float64)
    rets = np.diff(closes) / (closes[:-1] + 1e-12)
    var_95 = float(np.percentile(rets, 5)) if len(rets) >= 5 else 0.0
    cvar_95 = float(np.mean(rets[rets <= var_95])) if (rets <= var_95).any() else var_95
    return float(max(0.0, min(100.0, 50.0 - abs(cvar_95) * 1000.0))), {
        "var_95": var_95,
        "cvar_95": cvar_95,
    }


def _compute_liquidity(inp: SymbolDimScoreInput) -> tuple[float, dict[str, Any]]:
    if "volume" not in inp.ohlcv or len(inp.ohlcv["volume"]) < 2:
        raise ValueError(_INSUFFICIENT)
    vr = volume_ratio(volumes=inp.ohlcv["volume"])
    return float(min(100.0, max(0.0, 40.0 + vr * 10.0))), {"volume_ratio": vr}


def _compute_policy(inp: SymbolDimScoreInput) -> tuple[float, dict[str, Any]]:
    if not inp.news:
        return _FALLBACK_SCORE, {"events": 0}
    policies = [
        PolicyNews(content=str(n.get("content", "")), title=n.get("title")) for n in inp.news
    ]
    events = policy_event_extraction(policies=policies)
    if not events:
        return _FALLBACK_SCORE, {"events": 0}
    delta = sum(
        _SEVERITY_WEIGHT.get(ev.severity, 1.0)
        * (5.0 if ev.direction == "positive" else -5.0 if ev.direction == "negative" else 0.0)
        for ev in events
    )
    return float(max(0.0, min(100.0, 50.0 + delta))), {"events": len(events), "delta": delta}


def _compute_pattern(inp: SymbolDimScoreInput) -> tuple[float, dict[str, Any]]:
    ohlcv = inp.ohlcv
    if (
        not all(k in ohlcv for k in ("open", "high", "low", "close", "volume"))
        or len(ohlcv["close"]) < 2
    ):
        raise ValueError(_INSUFFICIENT)
    patterns = pattern_detection(
        ohlcv=OHLCVInput(
            open=ohlcv["open"],
            high=ohlcv["high"],
            low=ohlcv["low"],
            close=ohlcv["close"],
            volume=ohlcv["volume"],
        )
    )
    if not patterns:
        return _FALLBACK_SCORE, {"patterns": 0, "net": 0.0}
    net = sum(p.bullish_score - p.bearish_score for p in patterns)
    return float(max(0.0, min(100.0, 50.0 + net * 20.0))), {"patterns": len(patterns), "net": net}


_DIM_FNS: dict[str, Callable[[SymbolDimScoreInput], tuple[float, dict[str, Any]]]] = {
    "technical": _compute_technical,
    "fundamentals": _compute_fundamentals,
    "valuation": _compute_valuation,
    "sentiment": _compute_sentiment,
    "risk": _compute_risk,
    "liquidity": _compute_liquidity,
    "policy": _compute_policy,
    "pattern": _compute_pattern,
}


def symbol_dim_score(
    config: SymbolDimScoreConfig,
    input_data: SymbolDimScoreInput,
    output_dir: Path | None = None,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """8 维度综合评分. 纯同步, IO-free.

    Args:
        config: symbol + trade_date.
        input_data: 服务层预取数据 (ohlcv/financials/valuation/news).
        output_dir: 未使用 (report pillar 未启用); 保留签名兼容.
        on_step: 可选回调, 每个 dim 完成后以 step dict 调用.

    Returns:
        dict: scores, evidence, fingerprint, decision_trail, status, error.
    """
    from datetime import date as _date  # avoid shadowing at module level

    started_at = datetime.now(UTC)
    fingerprint = compute_fingerprint_for(config, input_data)
    cost_tracker = CostTracker()
    trail_steps: list[dict[str, Any]] = []
    scores: dict[str, float] = {}
    evidence: dict[str, dict[str, Any]] = {}
    status = "completed"
    error_info = None

    try:

        def _run_dim(name: str) -> tuple[str, float, dict[str, Any]]:
            step_start = datetime.now(UTC)
            try:
                s, ev = _DIM_FNS[name](input_data)
                step_status = "completed"
                step_err = None
            except Exception as exc:
                s = _FALLBACK_SCORE
                ev = {"error": str(exc), "status": _INSUFFICIENT}
                step_status = "partial"
                step_err = {"type": type(exc).__name__, "message": str(exc)}
            record_step(
                trail_steps=trail_steps,
                on_step=on_step,
                layer="oprim_batch",
                callable_name=f"compute_{name}",
                inputs_summary={"dim": name},
                outputs_summary={"score": s},
                started_at=step_start,
                status=step_status,
                error=step_err,
            )
            return name, s, ev

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_run_dim, dim): dim for dim in _DIM_FNS}
            for fut in as_completed(futures):
                dim, score, ev = fut.result()
                scores[dim] = score
                evidence[dim] = ev

    except Exception as exc:
        status = "failed"
        error_info = {"type": type(exc).__name__, "message": str(exc)}

    trail = build_decision_trail(
        fingerprint=fingerprint,
        config=config,
        input_data=input_data,
        trail_steps=trail_steps,
        cost_tracker=cost_tracker,
        started_at=started_at,
        status=status,
        error=error_info,
    )

    return {
        "scores": scores,
        "evidence": evidence,
        "fingerprint": fingerprint,
        "decision_trail": trail,
        "status": status,
        "error": error_info,
    }


# Re-export date for type annotations
from datetime import date  # noqa: E402
