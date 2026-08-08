"""Tests for omodul.consensus_workflow."""

from omodul.consensus_workflow import ConsensusConfig, consensus_workflow


def _sig(engine, score, promoted):
    d = "long" if score > 0 else ("short" if score < 0 else "neutral")
    return {
        "engine": engine,
        "direction": d,
        "score": score,
        "confidence": 1.0,
        "promoted": promoted,
        "age_seconds": 0,
    }


def test_completes_with_sentiment_and_onchain(tmp_path):
    inp = {
        "signals": [_sig("ta", 0.6, True), _sig("ml", 0.0, False)],
        "weights": {"ta": 1.0, "ml": 1.8},
        "regime_state": "range",
        "fgi": 20,
        "onchain": {"flow_in": 100, "flow_out": 300, "mvrv": 1.0},
    }
    r = consensus_workflow(ConsensusConfig(instrument="BTC-USDT-SWAP"), inp, tmp_path)
    assert r["status"] == "completed"
    f = r["findings"]
    assert f["final_direction"] == "long"
    assert f["n_promoted"] == 1
    assert f["sentiment_bias"] > 0  # FGI 20 = fear = bullish contrarian
    assert len(r["decision_trail"]["steps"]) == 2


def test_missing_sentiment_is_zero_bias(tmp_path):
    inp = {
        "signals": [_sig("ta", 0.6, True)],
        "weights": {"ta": 1.0},
        "regime_state": "range",
        "fgi": None,
        "onchain": None,
    }
    r = consensus_workflow(ConsensusConfig(), inp, tmp_path)
    assert r["status"] == "completed"
    assert r["findings"]["sentiment_bias"] == 0.0
    assert r["findings"]["onchain_bias"] == 0.0


def test_missing_signals_fails(tmp_path):
    r = consensus_workflow(ConsensusConfig(), {}, tmp_path)
    assert r["status"] == "failed"
    assert r["error"]["type"] == "KeyError"
