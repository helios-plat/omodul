"""Tests for high_dim_portfolio_workflow."""
from __future__ import annotations

import numpy as np
import pytest

from omodul.portfolio.high_dim_workflow import high_dim_portfolio_workflow


@pytest.fixture()
def rng():
    return np.random.default_rng(7)


@pytest.fixture()
def returns_60x5(rng):
    return rng.normal(0.001, 0.02, (60, 5))


@pytest.fixture()
def returns_100x15(rng):
    return rng.normal(0.001, 0.02, (100, 15))


class TestHighDimPortfolioWorkflow:
    def test_basic_output_keys(self, returns_60x5):
        result = high_dim_portfolio_workflow(returns_60x5)
        assert set(result.keys()) == {
            "hrp_weights", "ssd_weights", "clusters",
            "noise_threshold", "n_clusters"
        }

    def test_hrp_weights_shape(self, returns_60x5):
        result = high_dim_portfolio_workflow(returns_60x5)
        assert result["hrp_weights"].shape == (5,)

    def test_ssd_weights_shape(self, returns_60x5):
        result = high_dim_portfolio_workflow(returns_60x5)
        assert result["ssd_weights"].shape == (5,)

    def test_hrp_weights_sum_to_one(self, returns_60x5):
        result = high_dim_portfolio_workflow(returns_60x5)
        assert abs(result["hrp_weights"].sum() - 1.0) < 1e-6

    def test_ssd_weights_sum_to_one(self, returns_60x5):
        result = high_dim_portfolio_workflow(returns_60x5)
        assert abs(result["ssd_weights"].sum() - 1.0) < 1e-6

    def test_hrp_weights_non_negative(self, returns_60x5):
        result = high_dim_portfolio_workflow(returns_60x5)
        assert np.all(result["hrp_weights"] >= -1e-10)

    def test_ssd_weights_non_negative(self, returns_60x5):
        result = high_dim_portfolio_workflow(returns_60x5)
        assert np.all(result["ssd_weights"] >= -1e-10)

    def test_clusters_shape(self, returns_60x5):
        result = high_dim_portfolio_workflow(returns_60x5)
        assert result["clusters"].shape == (5,)

    def test_noise_threshold_positive(self, returns_60x5):
        result = high_dim_portfolio_workflow(returns_60x5)
        assert result["noise_threshold"] > 0

    def test_n_clusters_positive_integer(self, returns_60x5):
        result = high_dim_portfolio_workflow(returns_60x5)
        assert isinstance(result["n_clusters"], int)
        assert result["n_clusters"] >= 1

    def test_larger_dataset(self, returns_100x15):
        result = high_dim_portfolio_workflow(returns_100x15)
        assert result["hrp_weights"].shape == (15,)
        assert abs(result["hrp_weights"].sum() - 1.0) < 1e-6

    def test_raises_too_few_observations(self, rng):
        R = rng.normal(0, 0.02, (20, 4))
        with pytest.raises(ValueError, match="30"):
            high_dim_portfolio_workflow(R)

    def test_raises_too_few_assets(self, rng):
        R = rng.normal(0, 0.02, (50, 1))
        with pytest.raises(ValueError, match="2"):
            high_dim_portfolio_workflow(R)

    def test_raises_1d_input(self, rng):
        R = rng.normal(0, 0.02, (50,))
        with pytest.raises(ValueError, match="2-D"):
            high_dim_portfolio_workflow(R)
