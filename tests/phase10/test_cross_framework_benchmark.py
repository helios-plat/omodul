"""Tests for cross_framework_benchmark_report."""
from __future__ import annotations

import numpy as np
import pytest

from omodul.reporting.cross_framework_benchmark import cross_framework_benchmark_report


@pytest.fixture()
def rng():
    return np.random.default_rng(77)


@pytest.fixture()
def returns_80x5(rng):
    return rng.normal(0.001, 0.02, (80, 5))


@pytest.fixture()
def returns_120x8(rng):
    return rng.normal(0.001, 0.02, (120, 8))


class TestCrossFrameworkBenchmarkReport:
    def test_basic_output_keys(self, returns_80x5):
        result = cross_framework_benchmark_report(returns_80x5)
        assert set(result.keys()) == {"frameworks", "summary_table", "recommended_framework"}

    def test_frameworks_keys(self, returns_80x5):
        result = cross_framework_benchmark_report(returns_80x5)
        assert set(result["frameworks"].keys()) == {"eu", "cpt", "robust", "salience"}

    def test_per_framework_keys(self, returns_80x5):
        result = cross_framework_benchmark_report(returns_80x5)
        for name in ["eu", "cpt", "robust", "salience"]:
            fw = result["frameworks"][name]
            assert "weights" in fw
            assert "sharpe" in fw
            assert "max_drawdown" in fw

    def test_weights_sum_to_one(self, returns_80x5):
        result = cross_framework_benchmark_report(returns_80x5)
        for name in ["eu", "cpt", "robust", "salience"]:
            w = result["frameworks"][name]["weights"]
            assert abs(w.sum() - 1.0) < 1e-6, f"{name} weights don't sum to 1"

    def test_weights_non_negative(self, returns_80x5):
        result = cross_framework_benchmark_report(returns_80x5)
        for name in ["eu", "cpt", "robust", "salience"]:
            w = result["frameworks"][name]["weights"]
            assert np.all(w >= -1e-10), f"{name} has negative weights"

    def test_summary_table_length(self, returns_80x5):
        result = cross_framework_benchmark_report(returns_80x5)
        assert len(result["summary_table"]) == 4

    def test_summary_table_entries(self, returns_80x5):
        result = cross_framework_benchmark_report(returns_80x5)
        for row in result["summary_table"]:
            assert "framework" in row
            assert "sharpe" in row
            assert "max_drawdown" in row

    def test_recommended_framework_valid(self, returns_80x5):
        result = cross_framework_benchmark_report(returns_80x5)
        assert result["recommended_framework"] in ["eu", "cpt", "robust", "salience"]

    def test_recommended_framework_has_best_sharpe(self, returns_80x5):
        result = cross_framework_benchmark_report(returns_80x5)
        sharpes = {row["framework"]: row["sharpe"] for row in result["summary_table"]}
        best = max(sharpes, key=sharpes.get)
        assert result["recommended_framework"] == best

    def test_larger_dataset(self, returns_120x8):
        result = cross_framework_benchmark_report(returns_120x8)
        assert len(result["summary_table"]) == 4

    def test_raises_too_few_observations(self, rng):
        R = rng.normal(0, 0.02, (20, 4))
        with pytest.raises(ValueError, match="30"):
            cross_framework_benchmark_report(R)

    def test_raises_too_few_assets(self, rng):
        R = rng.normal(0, 0.02, (50, 1))
        with pytest.raises(ValueError, match="2"):
            cross_framework_benchmark_report(R)

    def test_raises_1d_input(self, rng):
        R = rng.normal(0, 0.02, (50,))
        with pytest.raises(ValueError, match="2-D"):
            cross_framework_benchmark_report(R)
