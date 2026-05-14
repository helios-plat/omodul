"""Tests for state_dependent_market_making_strategy."""
from __future__ import annotations

import numpy as np
import pytest

from omodul.microstructure.state_mm_strategy import state_dependent_market_making_strategy


@pytest.fixture()
def rng():
    return np.random.default_rng(55)


@pytest.fixture()
def basic_mm_inputs(rng):
    n_events = 50
    times = np.cumsum(rng.exponential(0.1, n_events))
    types = rng.integers(0, 2, n_events)
    ob = rng.uniform(0.4, 0.6, n_events)
    return times, types, ob


class TestStateMmStrategy:
    def test_basic_output_keys(self, basic_mm_inputs):
        times, types, ob = basic_mm_inputs
        result = state_dependent_market_making_strategy(
            times, types, ob, mid_price=100.0, sigma=0.01
        )
        assert set(result.keys()) == {
            "hawkes_params", "base_quotes", "state_adjusted_quotes", "adjustment_factor"
        }

    def test_adjusted_quotes_keys(self, basic_mm_inputs):
        times, types, ob = basic_mm_inputs
        result = state_dependent_market_making_strategy(
            times, types, ob, mid_price=100.0, sigma=0.01
        )
        aq = result["state_adjusted_quotes"]
        assert "bid" in aq
        assert "ask" in aq
        assert "optimal_spread" in aq

    def test_bid_less_than_ask(self, basic_mm_inputs):
        times, types, ob = basic_mm_inputs
        result = state_dependent_market_making_strategy(
            times, types, ob, mid_price=100.0, sigma=0.01
        )
        aq = result["state_adjusted_quotes"]
        assert aq["bid"] < aq["ask"]

    def test_adjustment_factor_at_least_one(self, basic_mm_inputs):
        times, types, ob = basic_mm_inputs
        result = state_dependent_market_making_strategy(
            times, types, ob, mid_price=100.0, sigma=0.01
        )
        assert result["adjustment_factor"] >= 1.0

    def test_adjusted_spread_geq_base(self, basic_mm_inputs):
        times, types, ob = basic_mm_inputs
        result = state_dependent_market_making_strategy(
            times, types, ob, mid_price=100.0, sigma=0.01
        )
        base_spread = float(result["base_quotes"].get("optimal_spread", 0.0))
        adj_spread = float(result["state_adjusted_quotes"]["optimal_spread"])
        assert adj_spread >= base_spread - 1e-10

    def test_hawkes_params_keys(self, basic_mm_inputs):
        times, types, ob = basic_mm_inputs
        result = state_dependent_market_making_strategy(
            times, types, ob, mid_price=100.0, sigma=0.01
        )
        hp = result["hawkes_params"]
        assert "branching_ratio" in hp
        assert "baseline" in hp

    def test_different_mid_price(self, basic_mm_inputs):
        times, types, ob = basic_mm_inputs
        result = state_dependent_market_making_strategy(
            times, types, ob, mid_price=200.0, sigma=0.01
        )
        assert result["state_adjusted_quotes"]["bid"] > 0

    def test_raises_non_increasing_times(self, rng):
        times = np.array([1.0, 2.0, 1.5, 3.0, 4.0] + list(np.cumsum(rng.exponential(0.1, 45))))
        types = rng.integers(0, 2, 50)
        ob = rng.uniform(0, 1, 50)
        with pytest.raises(ValueError, match="strictly increasing"):
            state_dependent_market_making_strategy(
                times, types, ob, mid_price=100.0, sigma=0.01
            )

    def test_raises_too_few_events(self, rng):
        times = np.cumsum(rng.exponential(0.1, 5))
        types = rng.integers(0, 2, 5)
        ob = rng.uniform(0, 1, 5)
        with pytest.raises(ValueError, match="10"):
            state_dependent_market_making_strategy(
                times, types, ob, mid_price=100.0, sigma=0.01
            )

    def test_raises_invalid_mid_price(self, basic_mm_inputs):
        times, types, ob = basic_mm_inputs
        with pytest.raises(ValueError, match="mid_price"):
            state_dependent_market_making_strategy(
                times, types, ob, mid_price=-1.0, sigma=0.01
            )

    def test_raises_mismatched_lengths(self, basic_mm_inputs):
        times, types, ob = basic_mm_inputs
        with pytest.raises(ValueError, match="same length"):
            state_dependent_market_making_strategy(
                times, types[:10], ob, mid_price=100.0, sigma=0.01
            )
