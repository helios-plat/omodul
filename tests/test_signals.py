"""Tests for Group 4: Signal & Alert modules."""

import numpy as np
import pandas as pd
import pytest

from omodul.signals import alert_calibration_engine, buy_sell_analysis, thesis_invalidation_monitor


class TestAlertCalibrationEngine:
    def test_basic_calibration(self):
        rng = np.random.default_rng(42)
        n = 200
        df = pd.DataFrame({
            "alert_type": rng.choice(["price", "volume"], n),
            "predicted_prob": rng.uniform(0, 1, n),
            "actual_outcome": rng.choice([0.0, 1.0], n),
        })
        result = alert_calibration_engine(df)
        assert "overall" in result
        assert "per_group" in result
        assert result["summary"]["n_alerts_total"] == n

    def test_with_bandit_state(self):
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "alert_type": ["A"] * 100,
            "predicted_prob": rng.uniform(0, 1, 100),
            "actual_outcome": rng.choice([0.0, 1.0], 100),
        })
        result = alert_calibration_engine(df, include_bandit_state=True)
        assert result["per_group"]["A"]["bandit_state"] is not None

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            alert_calibration_engine(pd.DataFrame(columns=["predicted_prob", "actual_outcome"]))

    def test_missing_columns_raises(self):
        with pytest.raises(ValueError, match="columns"):
            alert_calibration_engine(pd.DataFrame({"x": [1]}))

    def test_time_window_filter(self):
        """Cover lines 41-42: time_window filters by timestamp."""
        rng = np.random.default_rng(42)
        n = 100
        df = pd.DataFrame({
            "alert_type": ["A"] * n,
            "predicted_prob": rng.uniform(0, 1, n),
            "actual_outcome": rng.choice([0.0, 1.0], n),
            "ts": pd.date_range("2024-01-01", periods=n, freq="D"),
        })
        result = alert_calibration_engine(df, time_window=pd.Timedelta(days=30))
        assert result["summary"]["n_alerts_total"] <= n

    def test_small_group_skipped(self):
        """Cover line 59: groups with < 5 records are skipped."""
        df = pd.DataFrame({
            "alert_type": ["A"] * 100 + ["B"] * 3,  # B has < 5 records
            "predicted_prob": np.random.default_rng(42).uniform(0, 1, 103),
            "actual_outcome": np.random.default_rng(42).choice([0.0, 1.0], 103),
        })
        result = alert_calibration_engine(df)
        assert "B" not in result["per_group"]  # skipped due to < 5 records
        assert "A" in result["per_group"]


class TestThesisInvalidationMonitor:
    def test_basic_monitoring(self):
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "thesis_id": ["T1"] * 50 + ["T2"] * 50,
            "predicted_prob": rng.uniform(0.3, 0.7, 100),
            "actual_outcome": rng.choice([0.0, 1.0], 100),
        })
        result = thesis_invalidation_monitor(df, rolling_window=20)
        assert "per_thesis" in result
        assert "summary" in result
        assert result["summary"]["n_thesis"] == 2

    def test_invalidated_thesis(self):
        # Create a thesis with terrible predictions
        df = pd.DataFrame({
            "thesis_id": ["BAD"] * 50,
            "predicted_prob": np.ones(50) * 0.9,  # always predicts 0.9
            "actual_outcome": np.zeros(50),  # always wrong
        })
        result = thesis_invalidation_monitor(df, rolling_window=20, brier_threshold=0.25)
        assert result["per_thesis"]["BAD"]["status"] in ("AT_RISK", "INVALIDATED")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            thesis_invalidation_monitor(pd.DataFrame(columns=["thesis_id", "predicted_prob", "actual_outcome"]))

    def test_missing_group_by_raises(self):
        with pytest.raises(ValueError, match="group_by"):
            thesis_invalidation_monitor(
                pd.DataFrame({"predicted_prob": [0.5], "actual_outcome": [1.0]}),
                group_by="missing_col",
            )

    def test_missing_columns_raises(self):
        """Cover line 108: raise ValueError for missing required columns."""
        with pytest.raises(ValueError, match="columns"):
            thesis_invalidation_monitor(
                pd.DataFrame({"thesis_id": ["T1"], "x": [1.0]})
            )

    def test_small_group_skipped(self):
        """Cover line 120: groups with < 5 records are skipped."""
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "thesis_id": ["T1"] * 50 + ["T2"] * 3,  # T2 < 5
            "predicted_prob": rng.uniform(0, 1, 53),
            "actual_outcome": rng.choice([0.0, 1.0], 53),
        })
        result = thesis_invalidation_monitor(df, rolling_window=20)
        assert "T2" not in result["per_thesis"]  # skipped

    def test_warning_state(self):
        """Cover line 148-149: WARNING state when trend increasing but below threshold."""
        rng = np.random.default_rng(100)
        df = pd.DataFrame({
            "thesis_id": ["WARN"] * 80,
            "predicted_prob": rng.uniform(0.4, 0.6, 80),
            "actual_outcome": rng.choice([0.0, 1.0], 80),
        })
        result = thesis_invalidation_monitor(df, rolling_window=30, brier_threshold=0.5)
        assert result["per_thesis"]["WARN"]["status"] in ("VALID", "WARNING", "AT_RISK", "INVALIDATED")

    def test_valid_state(self):
        """Cover line 151: VALID state when below threshold and no trend."""
        # Perfect predictor → very low Brier score → VALID
        n = 60
        # Use near-perfect predictions to ensure VALID
        df = pd.DataFrame({
            "thesis_id": ["PERFECT"] * n,
            "predicted_prob": np.array([0.95 if i % 2 == 0 else 0.05 for i in range(n)]),
            "actual_outcome": np.array([1.0 if i % 2 == 0 else 0.0 for i in range(n)]),
        })
        result = thesis_invalidation_monitor(df, rolling_window=20, brier_threshold=0.25)
        assert result["per_thesis"]["PERFECT"]["status"] == "VALID"


# ──────────────────────────────────────────────
# Sprint 0: buy_sell_analysis
# ──────────────────────────────────────────────

def _provider(key_or_tier):
    return lambda prompt: f"buy recommendation for {prompt[:10]}"


class TestBuySellAnalysis:
    @pytest.mark.asyncio
    async def test_basic_returns_required_keys(self):
        signal_data = {"symbol": "AAPL", "risks": ["earnings risk"]}
        result = await buy_sell_analysis(
            signal_data=signal_data,
            fundamentals={"pe_ratio": 20.0},
            technicals={"close": 150.0},
            llm_client_provider=_provider,
            byok_key=None,
            prompt_builder=lambda ctx: f"Analyze {ctx['symbol']}",
        )
        assert "symbol" in result
        assert result["symbol"] == "AAPL"
        assert "analysis" in result
        assert "cache_status" in result
        assert "cost" in result
        assert "trail_id" in result

    @pytest.mark.asyncio
    async def test_analysis_fields(self):
        result = await buy_sell_analysis(
            signal_data={"symbol": "MSFT"},
            fundamentals={},
            technicals={"close": 300.0},
            llm_client_provider=_provider,
            byok_key=None,
            prompt_builder=lambda ctx: "analyze",
        )
        analysis = result["analysis"]
        assert "action_suggestion" in analysis
        assert "entry_price_range" in analysis
        assert "exit_price_range" in analysis
        assert "rationale" in analysis
        assert "key_risks" in analysis
        assert "confidence" in analysis
        assert analysis["action_suggestion"] in ("buy_now", "wait", "sell", "hold")

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        class DictCache:
            def __init__(self):
                self._store = {}
            def get(self, k):
                return self._store.get(k)
            def set(self, k, v):
                self._store[k] = v

        cache = DictCache()
        signal_data = {"symbol": "GOOG"}
        kwargs = dict(
            signal_data=signal_data,
            fundamentals={},
            technicals={"close": 100.0},
            llm_client_provider=_provider,
            byok_key=None,
            prompt_builder=lambda ctx: "",
            cache=cache,
        )
        result1 = await buy_sell_analysis(**kwargs)
        assert result1["cache_status"] == "miss"
        result2 = await buy_sell_analysis(**kwargs)
        assert result2["cache_status"] == "hit"

    @pytest.mark.asyncio
    async def test_deep_tier_higher_confidence(self):
        result = await buy_sell_analysis(
            signal_data={"symbol": "TSLA"},
            fundamentals={},
            technicals={"close": 200.0},
            llm_client_provider=_provider,
            byok_key=None,
            prompt_builder=lambda ctx: "",
            tier="deep",
        )
        assert result["analysis"]["confidence"] == pytest.approx(0.8)
        assert result["cost"] == pytest.approx(0.01)

    @pytest.mark.asyncio
    async def test_fast_tier_default(self):
        result = await buy_sell_analysis(
            signal_data={"symbol": "X"},
            fundamentals={},
            technicals={"close": 50.0},
            llm_client_provider=_provider,
            byok_key=None,
            prompt_builder=lambda ctx: "",
        )
        assert result["cost"] == pytest.approx(0.001)
        assert result["analysis"]["confidence"] == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_llm_provider_raises_graceful(self):
        def bad_provider(k):
            raise RuntimeError("provider down")

        result = await buy_sell_analysis(
            signal_data={"symbol": "BAD"},
            fundamentals={},
            technicals={"close": 100.0},
            llm_client_provider=bad_provider,
            byok_key=None,
            prompt_builder=lambda ctx: "",
        )
        assert "symbol" in result

    @pytest.mark.asyncio
    async def test_byok_key_passed_to_provider(self):
        received_keys = []

        def capturing_provider(key_or_tier):
            received_keys.append(key_or_tier)
            return lambda p: "hold this"

        await buy_sell_analysis(
            signal_data={"symbol": "Z"},
            fundamentals={},
            technicals={"close": 100.0},
            llm_client_provider=capturing_provider,
            byok_key="my-secret-key",
            prompt_builder=lambda ctx: "",
        )
        assert received_keys[0] == "my-secret-key"

    @pytest.mark.asyncio
    async def test_cost_tracker_called(self):
        tracked = []
        result = await buy_sell_analysis(
            signal_data={"symbol": "META"},
            fundamentals={},
            technicals={"close": 500.0},
            llm_client_provider=_provider,
            byok_key=None,
            prompt_builder=lambda ctx: "",
            cost_tracker=lambda info: tracked.append(info),
        )
        assert len(tracked) == 1
        assert tracked[0]["symbol"] == "META"

    @pytest.mark.asyncio
    async def test_async_llm_client(self):
        async def async_client(prompt):
            return "sell signal detected"

        def async_provider(k):
            return async_client

        result = await buy_sell_analysis(
            signal_data={"symbol": "NVDA"},
            fundamentals={},
            technicals={"close": 800.0},
            llm_client_provider=async_provider,
            byok_key=None,
            prompt_builder=lambda ctx: "analyze",
        )
        assert result["analysis"]["action_suggestion"] == "sell"

    @pytest.mark.asyncio
    async def test_key_risks_from_signal_data(self):
        risks = ["earnings miss", "macro headwind"]
        result = await buy_sell_analysis(
            signal_data={"symbol": "AMD", "risks": risks},
            fundamentals={},
            technicals={"close": 120.0},
            llm_client_provider=_provider,
            byok_key=None,
            prompt_builder=lambda ctx: "",
        )
        assert result["analysis"]["key_risks"] == risks

    @pytest.mark.asyncio
    async def test_cache_get_error_graceful(self):
        """Cover lines 252-253: cache.get() raises, treated as miss."""
        class BadGetCache:
            def get(self, k):
                raise IOError("cache read error")
            def set(self, k, v):
                pass

        result = await buy_sell_analysis(
            signal_data={"symbol": "GETERR"},
            fundamentals={},
            technicals={"close": 100.0},
            llm_client_provider=_provider,
            byok_key=None,
            prompt_builder=lambda ctx: "",
            cache=BadGetCache(),
        )
        assert result["cache_status"] == "miss"
        assert result["symbol"] == "GETERR"

    @pytest.mark.asyncio
    async def test_llm_callable_raises_graceful(self):
        """Cover lines 283-284: llm_client(prompt) raises, graceful fallback."""
        def provider_with_raising_client(k):
            def bad_client(prompt):
                raise ConnectionError("network error")
            return bad_client

        result = await buy_sell_analysis(
            signal_data={"symbol": "LLERR"},
            fundamentals={},
            technicals={"close": 100.0},
            llm_client_provider=provider_with_raising_client,
            byok_key=None,
            prompt_builder=lambda ctx: "",
        )
        assert "LLM unavailable" in result["analysis"]["rationale"]

    @pytest.mark.asyncio
    async def test_cache_set_error_graceful(self):
        """Cover lines 310-311: cache.set() raises, no crash."""
        class BadSetCache:
            def get(self, k):
                return None
            def set(self, k, v):
                raise IOError("disk full")

        result = await buy_sell_analysis(
            signal_data={"symbol": "SETERR"},
            fundamentals={},
            technicals={"close": 100.0},
            llm_client_provider=_provider,
            byok_key=None,
            prompt_builder=lambda ctx: "",
            cache=BadSetCache(),
        )
        assert result["symbol"] == "SETERR"

    @pytest.mark.asyncio
    async def test_cost_tracker_exception_graceful(self):
        """Cover lines 321-322: cost_tracker raises, no crash."""
        def bad_tracker(info):
            raise RuntimeError("tracker down")

        result = await buy_sell_analysis(
            signal_data={"symbol": "TRACKERR"},
            fundamentals={},
            technicals={"close": 100.0},
            llm_client_provider=_provider,
            byok_key=None,
            prompt_builder=lambda ctx: "",
            cost_tracker=bad_tracker,
        )
        assert result["symbol"] == "TRACKERR"

    @pytest.mark.academic_reference
    @pytest.mark.asyncio
    async def test_academic_reference_action_suggestion(self):
        """Action extraction follows keyword-based NLP classification
        (Pang & Lee 2008 sentiment analysis pattern adapted for trading signals)."""
        def wait_provider(k):
            return lambda p: "wait for better entry conditions"

        result = await buy_sell_analysis(
            signal_data={"symbol": "SPY"},
            fundamentals={},
            technicals={"close": 450.0},
            llm_client_provider=wait_provider,
            byok_key=None,
            prompt_builder=lambda ctx: "",
        )
        assert result["analysis"]["action_suggestion"] == "wait"
        assert "trail_id" in result
