"""Tests for omodul.candidate_pool (Tide v4 B5)."""

from __future__ import annotations

from datetime import date

from omodul.candidate_pool import (
    CandidatePoolConfig,
    CandidatePoolInput,
    candidate_pool,
    compute_fingerprint_for,
)

_DATE = date(2024, 3, 15)
_DIMS = [
    "technical",
    "fundamentals",
    "valuation",
    "sentiment",
    "risk",
    "liquidity",
    "policy",
    "pattern",
]


def _cfg(regime: str = "neutral", top_n: int = 5) -> CandidatePoolConfig:
    return CandidatePoolConfig(regime=regime, trade_date=_DATE, top_n=top_n)


def _universe(symbols: list[str]) -> list[dict]:
    return [{"symbol": s, "pe": 15.0, "is_st": False} for s in symbols]


def _dim_scores(symbols: list[str], base: float = 60.0) -> dict[str, dict[str, float]]:
    return {s: {d: base for d in _DIMS} for s in symbols}


def test_result_has_required_keys():
    inp = CandidatePoolInput(universe=_universe(["A", "B"]), dim_scores=_dim_scores(["A", "B"]))
    result = candidate_pool(_cfg(), inp)
    for k in ("candidates", "n_total", "n_after_filter", "regime", "fingerprint", "decision_trail"):
        assert k in result


def test_candidates_sorted_descending():
    dim_scores = {
        "A": {d: 80.0 for d in _DIMS},
        "B": {d: 40.0 for d in _DIMS},
        "C": {d: 60.0 for d in _DIMS},
    }
    inp = CandidatePoolInput(universe=_universe(["A", "B", "C"]), dim_scores=dim_scores)
    result = candidate_pool(_cfg(top_n=3), inp)
    ordered = [c[0] for c in result["candidates"]]
    assert ordered[0] == "A"
    assert ordered[-1] == "B"


def test_screen_filter_removes_symbols():
    universe = [
        {"symbol": "A", "pe": 10.0},
        {"symbol": "B", "pe": 60.0},
        {"symbol": "C", "pe": 20.0},
    ]
    rules = [{"field": "pe", "op": "lte", "threshold": 30.0, "reason": "high PE"}]
    inp = CandidatePoolInput(
        universe=universe, dim_scores=_dim_scores(["A", "B", "C"]), screen_rules=rules
    )
    result = candidate_pool(_cfg(top_n=10), inp)
    assert "B" not in {c[0] for c in result["candidates"]}
    assert result["n_after_filter"] == 2


def test_top_n_limits_output():
    symbols = [f"S{i}" for i in range(10)]
    inp = CandidatePoolInput(universe=_universe(symbols), dim_scores=_dim_scores(symbols))
    assert len(candidate_pool(_cfg(top_n=3), inp)["candidates"]) <= 3


def test_empty_universe_returns_empty():
    result = candidate_pool(_cfg(), CandidatePoolInput(universe=[]))
    assert result["candidates"] == []
    assert result["n_total"] == 0


def test_missing_dim_scores_fallback():
    inp = CandidatePoolInput(universe=_universe(["A", "B"]), dim_scores={})
    result = candidate_pool(_cfg(top_n=5), inp)
    assert len(result["candidates"]) == 2
    assert all(score == 50.0 for _, score in result["candidates"])


def test_regime_override_hot_vs_cold():
    dim_scores = {
        "A": {d: 50.0 for d in _DIMS} | {"technical": 90.0},
        "B": {d: 50.0 for d in _DIMS} | {"valuation": 90.0},
    }
    inp_hot = CandidatePoolInput(universe=_universe(["A", "B"]), dim_scores=dim_scores)
    inp_cold = CandidatePoolInput(universe=_universe(["A", "B"]), dim_scores=dim_scores)
    assert candidate_pool(_cfg(regime="hot", top_n=2), inp_hot)["candidates"][0][0] == "A"
    assert candidate_pool(_cfg(regime="cold", top_n=2), inp_cold)["candidates"][0][0] == "B"


def test_fingerprint_changes_with_regime():
    assert compute_fingerprint_for(_cfg(regime="hot"), None) != compute_fingerprint_for(
        _cfg(regime="cold"), None
    )


def test_fingerprint_stable():
    assert compute_fingerprint_for(_cfg(), None) == compute_fingerprint_for(_cfg(), None)


def test_decision_trail_metadata():
    inp = CandidatePoolInput(universe=_universe(["A"]), dim_scores=_dim_scores(["A"]))
    trail = candidate_pool(_cfg(), inp)["decision_trail"]
    assert isinstance(trail, dict)
    assert trail["omodul_name"] == "candidate_pool"
    assert trail["status"] == "completed"


def test_trail_has_2_steps():
    inp = CandidatePoolInput(universe=_universe(["A", "B"]), dim_scores=_dim_scores(["A", "B"]))
    steps = candidate_pool(_cfg(), inp)["decision_trail"]["steps"]
    assert len(steps) == 2
    names = {s["callable"] for s in steps}
    assert "apply_screen_filter" in names
    assert "regime_conditional_score_weighted" in names


def test_all_filtered_out_returns_empty():
    rules = [{"field": "pe", "op": "lt", "threshold": 1.0, "reason": "impossible"}]
    inp = CandidatePoolInput(
        universe=_universe(["A", "B"]), dim_scores=_dim_scores(["A", "B"]), screen_rules=rules
    )
    result = candidate_pool(_cfg(), inp)
    assert result["candidates"] == []
    assert result["n_after_filter"] == 0
