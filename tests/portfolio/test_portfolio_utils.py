"""Tests for omodul/portfolio/__init__.py — kelly, risk_parity, execution_cost_model."""
from __future__ import annotations
import pytest
from omodul.portfolio import kelly_allocator, risk_parity, execution_cost_model


class TestKellyAllocator:
    def test_basic(self):
        r = kelly_allocator(win_rate=0.55, avg_win=1.5, avg_loss=1.0)
        assert r["kelly_fraction"] > 0
        assert r["position_fraction"] <= r["kelly_fraction"]

    def test_half_kelly_reduces_fraction(self):
        # Use max_fraction=1.0 so the cap doesn't interfere
        full = kelly_allocator(0.6, 2.0, 1.0, half_kelly=False, max_fraction=1.0)
        half = kelly_allocator(0.6, 2.0, 1.0, half_kelly=True, max_fraction=1.0)
        assert half["position_fraction"] < full["position_fraction"]

    def test_max_fraction_cap(self):
        r = kelly_allocator(0.9, 10.0, 1.0, max_fraction=0.05)
        assert r["position_fraction"] <= 0.05 + 1e-9

    def test_zero_edge_returns_zero(self):
        r = kelly_allocator(0.0, 1.0, 1.0)
        assert r["position_fraction"] == 0.0

    def test_negative_edge_returns_zero_position(self):
        r = kelly_allocator(0.3, 0.5, 1.0)
        assert r["position_fraction"] == 0.0

    def test_with_cost_per_trade(self):
        r = kelly_allocator(0.6, 2.0, 1.0, cost_per_trade=0.5)
        r_no_cost = kelly_allocator(0.6, 2.0, 1.0, cost_per_trade=0.0)
        assert r["kelly_fraction"] < r_no_cost["kelly_fraction"]

    def test_output_keys(self):
        r = kelly_allocator(0.55, 1.5, 1.0)
        assert {"kelly_fraction", "position_fraction", "edge"} == set(r.keys())


class TestRiskParity:
    def test_basic_two_assets(self):
        weights = risk_parity({"A": 0.2, "B": 0.1})
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)
        assert weights["B"] > weights["A"]  # lower vol → higher weight

    def test_equal_vols(self):
        weights = risk_parity({"A": 0.3, "B": 0.3})
        assert weights["A"] == pytest.approx(0.5, abs=1e-6)

    def test_empty(self):
        assert risk_parity({}) == {}

    def test_zero_vol_excluded(self):
        weights = risk_parity({"A": 0.2, "B": 0.0})
        assert "A" in weights
        assert weights["A"] == pytest.approx(1.0, abs=1e-6)

    def test_target_total(self):
        weights = risk_parity({"A": 0.2, "B": 0.4}, target_total=2.0)
        assert sum(weights.values()) == pytest.approx(2.0, abs=1e-6)

    def test_all_zero_vols_equal_split(self):
        weights = risk_parity({"A": 0.0, "B": 0.0})
        assert weights["A"] == pytest.approx(0.5, abs=1e-6)


class TestExecutionCostModel:
    def test_basic(self):
        r = execution_cost_model(notional_usd=100_000.0, spread_bps=2.0)
        assert r["total_cost_bps"] > 0
        assert r["total_cost_usd"] > 0

    def test_zero_notional(self):
        r = execution_cost_model(notional_usd=0.0)
        assert r["total_cost_usd"] == pytest.approx(0.0, abs=1e-6)

    def test_higher_urgency_increases_timing_cost(self):
        low = execution_cost_model(100_000.0, urgency=0.0)
        high = execution_cost_model(100_000.0, urgency=1.0)
        assert high["timing_cost_bps"] > low["timing_cost_bps"]

    def test_output_keys(self):
        r = execution_cost_model(50_000.0)
        assert {"spread_cost_bps", "impact_cost_bps", "timing_cost_bps",
                "total_cost_bps", "total_cost_usd"} == set(r.keys())

    def test_larger_order_higher_impact(self):
        small = execution_cost_model(10_000.0, daily_volume_usd=1e8)
        large = execution_cost_model(1_000_000.0, daily_volume_usd=1e8)
        assert large["impact_cost_bps"] > small["impact_cost_bps"]
