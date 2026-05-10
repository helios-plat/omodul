"""Tests for Group 6: Data Quality modules."""

import numpy as np
import pandas as pd
import pytest

from omodul.data_quality import cross_source_consistency_check, panel_data_quality_check


class TestPanelDataQualityCheck:
    def test_basic_check(self, btc_panel):
        result = panel_data_quality_check(btc_panel)
        assert "per_field" in result
        assert "overall_score" in result
        assert 0 <= result["overall_score"] <= 1

    def test_with_baseline(self, btc_panel):
        baseline = btc_panel.iloc[:100]
        result = panel_data_quality_check(btc_panel.iloc[100:], baseline_panel=baseline)
        assert "drift" in result["per_field"][btc_panel.columns[0]]

    def test_with_gaps(self):
        dates = pd.date_range("2023-01-01", periods=100, freq="D")
        data = pd.DataFrame({"price": np.random.default_rng(42).normal(100, 10, 100)}, index=dates)
        data.iloc[10:15, 0] = np.nan  # introduce NaN gaps
        result = panel_data_quality_check(data)
        # Outliers or gap detection should flag issues
        assert result["overall_score"] < 1.0

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            panel_data_quality_check(pd.DataFrame())

    def test_no_numeric_raises(self):
        with pytest.raises(ValueError, match="numeric"):
            panel_data_quality_check(pd.DataFrame({"text": ["a", "b", "c"]}))

    def test_real_data_dogfood(self, btc_panel):
        """End-to-end with real BTC panel."""
        result = panel_data_quality_check(btc_panel)
        assert result["panel_metadata"]["n_rows"] == 365
        assert result["overall_score"] > 0.5


class TestCrossSourceConsistencyCheck:
    def test_consistent_sources(self):
        rng = np.random.default_rng(42)
        base = rng.normal(100, 10, 200)
        data = pd.DataFrame({
            "source_a": base + rng.normal(0, 0.5, 200),
            "source_b": base + rng.normal(0, 0.5, 200),
            "source_c": base + rng.normal(0, 0.5, 200),
        })
        result = cross_source_consistency_check(data, consistency_threshold_corr=0.8)
        assert result["summary"]["all_consistent"]
        assert result["recommended_source"] is not None

    def test_inconsistent_source(self):
        rng = np.random.default_rng(42)
        data = pd.DataFrame({
            "good_a": rng.normal(100, 10, 200),
            "good_b": rng.normal(100, 10, 200),
            "bad": rng.normal(500, 50, 200),  # very different
        })
        result = cross_source_consistency_check(data)
        assert not result["summary"]["all_consistent"]

    def test_with_outlier_detection(self):
        rng = np.random.default_rng(42)
        data = pd.DataFrame({
            "a": rng.normal(0, 1, 100),
            "b": rng.normal(0, 1, 100),
        })
        result = cross_source_consistency_check(data, include_outlier_detection=True)
        assert "outlier_periods" in result

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            cross_source_consistency_check(pd.DataFrame())

    def test_single_source_raises(self):
        with pytest.raises(ValueError, match="at least 2"):
            cross_source_consistency_check(pd.DataFrame({"a": [1, 2, 3]}))
