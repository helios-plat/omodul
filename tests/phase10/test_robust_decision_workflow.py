"""Tests for robust_decision_workflow."""
from __future__ import annotations

import numpy as np
import pytest

from omodul.robust.decision_workflow import robust_decision_workflow


@pytest.fixture()
def rng():
    return np.random.default_rng(13)


@pytest.fixture()
def returns_60x4(rng):
    return rng.normal(0.001, 0.02, (60, 4))


@pytest.fixture()
def returns_120x6(rng):
    return rng.normal(0.001, 0.02, (120, 6))


class TestRobustDecisionWorkflow:
    def test_basic_output_keys(self, returns_60x4):
        result = robust_decision_workflow(returns_60x4)
        assert set(result.keys()) == {"robust_weights", "individual", "weight_dispersion"}

    def test_individual_keys(self, returns_60x4):
        result = robust_decision_workflow(returns_60x4)
        assert set(result["individual"].keys()) == {
            "multiplier", "variational", "smooth_ambiguity", "maxmin"
        }

    def test_robust_weights_shape(self, returns_60x4):
        result = robust_decision_workflow(returns_60x4)
        assert result["robust_weights"].shape == (4,)

    def test_robust_weights_sum_to_one(self, returns_60x4):
        result = robust_decision_workflow(returns_60x4)
        assert abs(result["robust_weights"].sum() - 1.0) < 1e-6

    def test_individual_weights_sum_to_one(self, returns_60x4):
        result = robust_decision_workflow(returns_60x4)
        for name, w in result["individual"].items():
            assert abs(w.sum() - 1.0) < 1e-6, f"{name} weights don't sum to 1"

    def test_individual_weights_non_negative(self, returns_60x4):
        result = robust_decision_workflow(returns_60x4)
        for name, w in result["individual"].items():
            assert np.all(w >= -1e-10), f"{name} has negative weights"

    def test_weight_dispersion_non_negative(self, returns_60x4):
        result = robust_decision_workflow(returns_60x4)
        assert result["weight_dispersion"] >= 0.0

    def test_weight_dispersion_is_float(self, returns_60x4):
        result = robust_decision_workflow(returns_60x4)
        assert isinstance(result["weight_dispersion"], float)

    def test_larger_dataset(self, returns_120x6):
        result = robust_decision_workflow(returns_120x6)
        assert result["robust_weights"].shape == (6,)
        assert abs(result["robust_weights"].sum() - 1.0) < 1e-6

    def test_different_theta_values(self, returns_60x4):
        r1 = robust_decision_workflow(returns_60x4, theta=0.5)
        r2 = robust_decision_workflow(returns_60x4, theta=5.0)
        # Both should produce valid weights
        assert abs(r1["robust_weights"].sum() - 1.0) < 1e-6
        assert abs(r2["robust_weights"].sum() - 1.0) < 1e-6

    def test_raises_too_few_observations(self, rng):
        R = rng.normal(0, 0.02, (20, 4))
        with pytest.raises(ValueError, match="30"):
            robust_decision_workflow(R)

    def test_raises_too_few_assets(self, rng):
        R = rng.normal(0, 0.02, (50, 1))
        with pytest.raises(ValueError, match="2"):
            robust_decision_workflow(R)

    def test_raises_invalid_theta(self, returns_60x4):
        with pytest.raises(ValueError, match="theta"):
            robust_decision_workflow(returns_60x4, theta=0.0)

    def test_raises_negative_theta(self, returns_60x4):
        with pytest.raises(ValueError, match="theta"):
            robust_decision_workflow(returns_60x4, theta=-1.0)
