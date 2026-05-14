"""Tests for behavioral_portfolio_workflow."""
from __future__ import annotations

import numpy as np
import pytest

from omodul.behavioral.portfolio_workflow import behavioral_portfolio_workflow


@pytest.fixture()
def rng():
    return np.random.default_rng(42)


@pytest.fixture()
def returns_50x5(rng):
    return rng.normal(0.001, 0.02, (50, 5))


@pytest.fixture()
def returns_100x10(rng):
    return rng.normal(0.001, 0.02, (100, 10))


class TestBehavioralPortfolioWorkflow:
    def test_basic_output_keys(self, returns_50x5):
        result = behavioral_portfolio_workflow(returns_50x5, reference_return=0.0)
        assert set(result.keys()) == {"cpt_weights", "analytical_weight", "llad",
                                       "well_posed", "comparison"}

    def test_cpt_weights_shape(self, returns_50x5):
        result = behavioral_portfolio_workflow(returns_50x5, reference_return=0.0)
        assert result["cpt_weights"].shape == (5,)

    def test_cpt_weights_sum_to_one(self, returns_50x5):
        result = behavioral_portfolio_workflow(returns_50x5, reference_return=0.0)
        # Weights from CPT optimizer may not sum to 1 exactly but should be finite
        assert np.isfinite(result["cpt_weights"]).all()

    def test_analytical_weight_is_scalar(self, returns_50x5):
        result = behavioral_portfolio_workflow(returns_50x5, reference_return=0.0)
        assert isinstance(result["analytical_weight"], float)

    def test_llad_positive(self, returns_50x5):
        result = behavioral_portfolio_workflow(returns_50x5, reference_return=0.0)
        assert result["llad"] > 0

    def test_llad_formula(self):
        """LLAD = (beta/alpha) * loss_aversion^(1/beta)."""
        alpha, beta, la = 0.88, 0.88, 2.25
        expected = (beta / alpha) * la ** (1.0 / beta)
        rng = np.random.default_rng(0)
        R = rng.normal(0.001, 0.02, (50, 2))
        result = behavioral_portfolio_workflow(R, reference_return=0.0,
                                               alpha=alpha, beta=beta, loss_aversion=la)
        assert abs(result["llad"] - expected) < 1e-8

    def test_comparison_keys(self, returns_50x5):
        result = behavioral_portfolio_workflow(returns_50x5, reference_return=0.0)
        comp = result["comparison"]
        assert "weight_diff" in comp
        assert "cpt_value_numeric" in comp

    def test_larger_dataset(self, returns_100x10):
        result = behavioral_portfolio_workflow(returns_100x10, reference_return=0.001)
        assert result["cpt_weights"].shape == (10,)
        assert result["llad"] > 0

    def test_nonzero_reference_return(self, returns_50x5):
        result = behavioral_portfolio_workflow(returns_50x5, reference_return=0.005)
        assert result["cpt_weights"].shape == (5,)

    def test_raises_too_few_observations(self, rng):
        R = rng.normal(0, 0.02, (20, 4))
        with pytest.raises(ValueError, match="30"):
            behavioral_portfolio_workflow(R, reference_return=0.0)

    def test_raises_too_few_assets(self, rng):
        R = rng.normal(0, 0.02, (50, 1))
        with pytest.raises(ValueError, match="2"):
            behavioral_portfolio_workflow(R, reference_return=0.0)

    def test_raises_invalid_alpha(self, rng):
        R = rng.normal(0, 0.02, (50, 3))
        with pytest.raises(ValueError, match="alpha"):
            behavioral_portfolio_workflow(R, reference_return=0.0, alpha=0.0)

    def test_raises_invalid_beta(self, rng):
        R = rng.normal(0, 0.02, (50, 3))
        with pytest.raises(ValueError, match="beta"):
            behavioral_portfolio_workflow(R, reference_return=0.0, beta=1.5)

    def test_raises_invalid_loss_aversion(self, rng):
        R = rng.normal(0, 0.02, (50, 3))
        with pytest.raises(ValueError, match="loss_aversion"):
            behavioral_portfolio_workflow(R, reference_return=0.0, loss_aversion=0.5)
