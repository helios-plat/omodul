"""Tests for Group 2: Regime modules."""

import numpy as np
import pandas as pd
import pytest

from omodul.regime import (
    regime_change_detector,
    regime_conditional_dashboard_data,
    regime_replay_search,
)


class TestRegimeReplaySearch:
    def test_basic_search(self):
        rng = np.random.default_rng(42)
        current = pd.DataFrame({"price": rng.normal(0, 1, 30)})
        historical = [
            {"panel": pd.DataFrame({"price": rng.normal(0, 1, 30)}),
             "forward_returns": rng.normal(0.001, 0.01, 20)}
            for _ in range(5)
        ]
        result = regime_replay_search(current, historical, top_k=3)
        assert "top_k_matches" in result
        assert len(result["top_k_matches"]) <= 3
        assert result["n_matches_used"] > 0

    def test_with_forward_distribution(self):
        rng = np.random.default_rng(42)
        current = pd.DataFrame({"price": rng.normal(0, 1, 30)})
        historical = [
            {"panel": pd.DataFrame({"price": rng.normal(0, 1, 30)}),
             "forward_returns": rng.normal(0.001, 0.01, 30)}
            for _ in range(10)
        ]
        result = regime_replay_search(current, historical, forward_days=20)
        assert result["forward_distribution"] is not None

    def test_empty_historical_raises(self):
        with pytest.raises(ValueError):
            regime_replay_search(pd.DataFrame({"x": [1, 2, 3]}), [])

    def test_empty_panel_raises(self):
        with pytest.raises(ValueError):
            regime_replay_search(pd.DataFrame(), [{"panel": pd.DataFrame({"x": [1]})}])


class TestRegimeChangeDetector:
    def test_basic_detection(self, spy_returns, regime_labels):
        data = pd.DataFrame({"returns": spy_returns})
        result = regime_change_detector(data, regime_labels)
        assert "transitions" in result
        assert result["n_transitions"] >= 0

    def test_with_transition_history(self, spy_returns, regime_labels):
        data = pd.DataFrame({"returns": spy_returns})
        result = regime_change_detector(data, regime_labels, include_transition_history=True)
        assert "transition_history_summary" in result

    def test_short_data_raises(self):
        with pytest.raises(ValueError, match="too short"):
            regime_change_detector(
                pd.DataFrame({"x": [1, 2, 3]}),
                pd.Series(["A", "B", "A"]),
                window_before=30, window_after=30,
            )

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="same length"):
            regime_change_detector(pd.DataFrame({"x": [1, 2]}), pd.Series(["A"]))


class TestRegimeConditionalDashboard:
    def test_basic_dashboard(self, spy_returns, regime_labels):
        result = regime_conditional_dashboard_data(spy_returns, regime_labels)
        assert "per_regime_metrics" in result
        assert "summary" in result
        assert result["summary"]["n_regimes"] == 3

    def test_with_pairwise_shift(self, spy_returns, regime_labels):
        result = regime_conditional_dashboard_data(spy_returns, regime_labels, include_pairwise_shift=True)
        assert result["pairwise_shift_matrix"] is not None

    def test_without_transitions(self, spy_returns, regime_labels):
        result = regime_conditional_dashboard_data(spy_returns, regime_labels, include_transitions=False)
        assert result["transition_analysis"] is None

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            regime_conditional_dashboard_data(pd.Series([1, 2]), pd.Series(["A"]))
