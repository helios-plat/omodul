"""Tests for 3 Helios crypto omoduls (fusion_score, market_summary, timeframes)."""

from pathlib import Path

from omodul.fusion_score_workflow import (
    FusionScoreConfig,
    compute_fingerprint_for,
    fusion_score_workflow,
)
from omodul.market_summary_workflow import MarketSummaryConfig, market_summary_workflow
from omodul.timeframes_compute_workflow import TimeframesConfig, timeframes_compute_workflow

# ─── fusion_score_workflow ────────────────────────────────────────────────────


class TestFusionScoreWorkflow:
    def _config(self):
        return FusionScoreConfig(
            symbol="BTC-USDT", snapshot_ts="2024-01-01T00:00:00Z", pack_id="default"
        )

    def _input(self):
        return {
            "dimensions": [
                {"name": "trend", "value": 30, "side": "long"},
                {"name": "flow", "value": 20, "side": "long"},
                {"name": "sentiment", "value": -10, "side": "short"},
            ],
            "weights": {"trend": 0.3, "flow": 0.2, "sentiment": 0.15},
            "redlines": [],
            "adjustments": {"seasonality": 5},
        }

    def test_completed(self, tmp_path):
        r = fusion_score_workflow(self._config(), self._input(), tmp_path)
        assert r["status"] == "completed"
        assert r["findings"]["finalScore"] != 0

    def test_fingerprint_deterministic(self, tmp_path):
        c, i = self._config(), self._input()
        r1 = fusion_score_workflow(c, i, tmp_path)
        r2 = fusion_score_workflow(c, i, tmp_path)
        assert r1["fingerprint"] == r2["fingerprint"]

    def test_decision_trail_has_steps(self, tmp_path):
        r = fusion_score_workflow(self._config(), self._input(), tmp_path)
        assert len(r["decision_trail"]["steps"]) >= 3

    def test_report_generated(self, tmp_path):
        r = fusion_score_workflow(self._config(), self._input(), tmp_path)
        assert r["report_path"] is not None
        assert Path(r["report_path"]).exists()

    def test_redline_breached_zeroes_score(self, tmp_path):
        inp = self._input()
        inp["redlines"] = [{"name": "liquidity", "triggered": True}]
        r = fusion_score_workflow(self._config(), inp, tmp_path)
        assert r["findings"]["finalScore"] == 0

    def test_on_step_callback(self, tmp_path):
        steps = []
        fusion_score_workflow(self._config(), self._input(), tmp_path, on_step=steps.append)
        assert len(steps) >= 3

    def test_compute_fingerprint_for(self):
        c = self._config()
        fp = compute_fingerprint_for(c, self._input())
        assert len(fp) == 64

    def test_empty_dimensions(self, tmp_path):
        inp = {"dimensions": [], "weights": {}, "redlines": [], "adjustments": {}}
        r = fusion_score_workflow(self._config(), inp, tmp_path)
        assert r["status"] == "completed"
        assert r["findings"]["finalScore"] == 0

    def test_cost_is_zero(self, tmp_path):
        r = fusion_score_workflow(self._config(), self._input(), tmp_path)
        assert r["cost_usd"] == 0.0

    def test_pillars_enabled(self):
        assert "fingerprint" in FusionScoreConfig._enabled_pillars
        assert "decision_trail" in FusionScoreConfig._enabled_pillars
        assert "report" in FusionScoreConfig._enabled_pillars


# ─── market_summary_workflow ──────────────────────────────────────────────────


class TestMarketSummaryWorkflow:
    def _config(self):
        return MarketSummaryConfig(symbol="BTC", date="2024-01-01", model="qwen-max")

    def test_completed_with_cache(self, tmp_path):
        r = market_summary_workflow(self._config(), {"cached_summary": "cached text"}, tmp_path)
        assert r["status"] == "completed"
        assert r["findings"]["source"] == "cache"

    def test_completed_without_cache(self, tmp_path):
        r = market_summary_workflow(self._config(), {"context_sources": ["src1", "src2"]}, tmp_path)
        assert r["status"] == "completed"
        assert r["findings"]["source"] == "llm"

    def test_fingerprint_deterministic(self, tmp_path):
        c, i = self._config(), {"context_sources": ["a"]}
        assert (
            market_summary_workflow(c, i, tmp_path)["fingerprint"]
            == market_summary_workflow(c, i, tmp_path)["fingerprint"]
        )

    def test_decision_trail(self, tmp_path):
        r = market_summary_workflow(self._config(), {"context_sources": ["x"]}, tmp_path)
        assert len(r["decision_trail"]["steps"]) >= 2

    def test_report_generated(self, tmp_path):
        r = market_summary_workflow(self._config(), {"context_sources": ["x"]}, tmp_path)
        assert r["report_path"] is not None

    def test_cost_tracked(self, tmp_path):
        r = market_summary_workflow(self._config(), {"context_sources": ["x"]}, tmp_path)
        assert r["cost_usd"] == 0.0  # No real LLM call in extraction

    def test_on_step_callback(self, tmp_path):
        steps = []
        market_summary_workflow(
            self._config(), {"context_sources": ["x"]}, tmp_path, on_step=steps.append
        )
        assert len(steps) >= 2

    def test_pillars_include_cost(self):
        assert "cost" in MarketSummaryConfig._enabled_pillars

    def test_empty_sources(self, tmp_path):
        r = market_summary_workflow(self._config(), {"context_sources": []}, tmp_path)
        assert r["status"] == "completed"

    def test_partial_findings_on_cache(self, tmp_path):
        r = market_summary_workflow(self._config(), {"cached_summary": "x"}, tmp_path)
        assert r["findings"]["summary"] == "x"


# ─── timeframes_compute_workflow ──────────────────────────────────────────────


class TestTimeframesComputeWorkflow:
    def _config(self):
        return TimeframesConfig(symbol="BTC-USDT", snapshot_ts="2024-01-01T00:00:00Z")

    def _input(self):
        return {
            "klines": {
                "1M": [{"close": 40000}, {"close": 50000}],
                "1w": [{"close": 48000}, {"close": 50000}],
                "1d": [{"close": 49000}, {"close": 50000}],
                "4h": [{"close": 49500}, {"close": 50000}],
                "1h": [{"close": 49800}, {"close": 50000}],
            },
            "fgi": 72,
        }

    def test_completed(self, tmp_path):
        r = timeframes_compute_workflow(self._config(), self._input(), tmp_path)
        assert r["status"] == "completed"
        assert "tf1" in r["findings"]

    def test_fingerprint(self, tmp_path):
        c, i = self._config(), self._input()
        assert (
            timeframes_compute_workflow(c, i, tmp_path)["fingerprint"]
            == timeframes_compute_workflow(c, i, tmp_path)["fingerprint"]
        )

    def test_decision_trail_steps(self, tmp_path):
        r = timeframes_compute_workflow(self._config(), self._input(), tmp_path)
        assert len(r["decision_trail"]["steps"]) >= 4

    def test_no_report(self, tmp_path):
        r = timeframes_compute_workflow(self._config(), self._input(), tmp_path)
        assert r["report_path"] is None

    def test_no_cost(self, tmp_path):
        r = timeframes_compute_workflow(self._config(), self._input(), tmp_path)
        assert r["cost_usd"] == 0.0

    def test_tf1_bullish_on_high_fgi(self, tmp_path):
        r = timeframes_compute_workflow(self._config(), self._input(), tmp_path)
        assert r["findings"]["tf1"]["strategic"]["state"] == "bullish"

    def test_tf2_alignment(self, tmp_path):
        r = timeframes_compute_workflow(self._config(), self._input(), tmp_path)
        assert r["findings"]["tf2"]["trend"]["alignment"] == "aligned"

    def test_on_step_callback(self, tmp_path):
        steps = []
        timeframes_compute_workflow(self._config(), self._input(), tmp_path, on_step=steps.append)
        assert len(steps) >= 4

    def test_empty_klines(self, tmp_path):
        r = timeframes_compute_workflow(self._config(), {"klines": {}, "fgi": None}, tmp_path)
        assert r["status"] == "completed"

    def test_pillars_no_report_no_cost(self):
        assert "report" not in TimeframesConfig._enabled_pillars
        assert "cost" not in TimeframesConfig._enabled_pillars
        assert "fingerprint" in TimeframesConfig._enabled_pillars
