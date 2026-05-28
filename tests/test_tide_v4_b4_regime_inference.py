"""Tests for omodul.regime_inference (Tide v4 B4)."""

from __future__ import annotations

from datetime import date

from omodul.regime_inference import (
    RegimeInferenceConfig,
    RegimeInferenceInput,
    compute_fingerprint_for,
    regime_inference,
)

_DATE = date(2024, 3, 15)
_HOT = {"limit_up_count": 50.0, "broken_rate": 0.1}
_NEUTRAL = {"limit_up_count": 10.0, "broken_rate": 0.25}

_COLD_DEFS = [
    {
        "name": "cold",
        "conditions": [{"field": "broken_rate", "op": "gte", "value": 0.5}],
        "priority": 1,
    },
    {
        "name": "neutral",
        "conditions": [{"field": "broken_rate", "op": "lt", "value": 0.5}],
        "priority": 2,
    },
]


def _cfg(**kw) -> RegimeInferenceConfig:
    return RegimeInferenceConfig(trade_date=_DATE, **kw)


def _hist(state: str, n: int = 3) -> list[dict]:
    return [{"date": f"2024-03-{i + 1:02d}", "state": state, "confidence": 0.9} for i in range(n)]


def test_result_has_required_keys():
    result = regime_inference(_cfg(), RegimeInferenceInput(today_indicators=_HOT, raw_history=[]))
    for k in (
        "regime",
        "raw_regime",
        "confidence",
        "state_changed",
        "persistence_days",
        "fingerprint",
        "decision_trail",
    ):
        assert k in result


def test_hot_indicators_classify_hot():
    result = regime_inference(
        _cfg(), RegimeInferenceInput(today_indicators=_HOT, raw_history=_hist("hot", 3))
    )
    assert result["raw_regime"] in {"hot", "extreme_hot"}


def test_cold_custom_defs():
    cfg = _cfg(state_definitions=_COLD_DEFS)
    result = regime_inference(
        cfg, RegimeInferenceInput(today_indicators={"broken_rate": 0.65}, raw_history=[])
    )
    assert result["raw_regime"] == "cold"


def test_smoothing_holds_old_state_below_threshold():
    inp = RegimeInferenceInput(
        today_indicators=_NEUTRAL, raw_history=_hist("neutral", 1), current_smoothed_state="hot"
    )
    assert regime_inference(_cfg(), inp)["state_changed"] is False
    assert regime_inference(_cfg(), inp)["regime"] == "hot"


def test_smoothing_confirms_switch_after_threshold():
    inp = RegimeInferenceInput(
        today_indicators=_NEUTRAL, raw_history=_hist("neutral", 4), current_smoothed_state="hot"
    )
    assert regime_inference(_cfg(), inp)["state_changed"] is True


def test_persistence_days_counted():
    inp = RegimeInferenceInput(
        today_indicators=_HOT, raw_history=_hist("hot", 5), current_smoothed_state="hot"
    )
    assert regime_inference(_cfg(), inp)["persistence_days"] >= 1


def test_empty_history_returns_result():
    result = regime_inference(
        _cfg(), RegimeInferenceInput(today_indicators=_NEUTRAL, raw_history=[])
    )
    assert "regime" in result


def test_fingerprint_changes_with_date():
    assert compute_fingerprint_for(_cfg(), None) != compute_fingerprint_for(
        RegimeInferenceConfig(trade_date=date(2024, 1, 2)), None
    )


def test_fingerprint_changes_with_window():
    assert compute_fingerprint_for(_cfg(smoothing_window=3), None) != compute_fingerprint_for(
        _cfg(smoothing_window=7), None
    )


def test_decision_trail_metadata():
    result = regime_inference(
        _cfg(), RegimeInferenceInput(today_indicators=_HOT, raw_history=_hist("hot", 2))
    )
    trail = result["decision_trail"]
    assert isinstance(trail, dict)
    assert trail["omodul_name"] == "regime_inference"
    assert trail["status"] == "completed"


def test_trail_has_2_steps():
    result = regime_inference(
        _cfg(), RegimeInferenceInput(today_indicators=_HOT, raw_history=_hist("hot", 2))
    )
    steps = result["decision_trail"]["steps"]
    assert len(steps) == 2
    names = {s["callable"] for s in steps}
    assert "multi_state_classify" in names
    assert "regime_smoothing" in names


def test_custom_state_definitions():
    custom = [
        {
            "name": "bull",
            "conditions": [{"field": "rsi", "op": "gte", "value": 60.0}],
            "priority": 1,
        },
        {
            "name": "bear",
            "conditions": [{"field": "rsi", "op": "lt", "value": 40.0}],
            "priority": 2,
        },
    ]
    cfg = _cfg(state_definitions=custom)
    result = regime_inference(
        cfg, RegimeInferenceInput(today_indicators={"rsi": 70.0}, raw_history=[])
    )
    assert result["raw_regime"] == "bull"
