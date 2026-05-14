"""Tests for systemic_risk_dashboard."""
from __future__ import annotations

import numpy as np
import pytest

from omodul.risk.systemic_dashboard import systemic_risk_dashboard


@pytest.fixture()
def rng():
    return np.random.default_rng(99)


@pytest.fixture()
def returns_60x4(rng):
    return rng.normal(0.001, 0.02, (60, 4))


@pytest.fixture()
def returns_100x8(rng):
    return rng.normal(0.001, 0.02, (100, 8))


class TestSystemicRiskDashboard:
    def test_basic_output_keys(self, returns_60x4):
        result = systemic_risk_dashboard(returns_60x4)
        assert set(result.keys()) == {
            "systemic_metrics", "network_centrality", "clearing_result", "risk_summary"
        }

    def test_systemic_metrics_has_covar_mes(self, returns_60x4):
        result = systemic_risk_dashboard(returns_60x4)
        assert "covar" in result["systemic_metrics"]
        assert "mes" in result["systemic_metrics"]

    def test_systemic_metrics_shape(self, returns_60x4):
        result = systemic_risk_dashboard(returns_60x4)
        assert result["systemic_metrics"]["covar"].shape == (4,)
        assert result["systemic_metrics"]["mes"].shape == (4,)

    def test_network_centrality_keys(self, returns_60x4):
        result = systemic_risk_dashboard(returns_60x4)
        assert "debt_rank" in result["network_centrality"]
        assert "eigenvector" in result["network_centrality"]

    def test_centrality_sums_to_one(self, returns_60x4):
        result = systemic_risk_dashboard(returns_60x4)
        assert abs(result["network_centrality"]["debt_rank"].sum() - 1.0) < 1e-6

    def test_clearing_result_keys(self, returns_60x4):
        result = systemic_risk_dashboard(returns_60x4)
        cr = result["clearing_result"]
        assert "clearing_vector" in cr
        assert "default_status" in cr
        assert "recovery_rates" in cr

    def test_risk_summary_keys(self, returns_60x4):
        result = systemic_risk_dashboard(returns_60x4)
        rs = result["risk_summary"]
        assert "n_institutions" in rs
        assert "n_defaults" in rs
        assert rs["n_institutions"] == 4

    def test_custom_liabilities_matrix(self, returns_60x4):
        N = 4
        liab = np.eye(N) * 0.0  # off-diagonal = 0
        liab[0, 1] = 0.1
        liab[1, 2] = 0.1
        result = systemic_risk_dashboard(returns_60x4, liabilities=liab)
        assert result["risk_summary"]["n_institutions"] == 4

    def test_larger_dataset(self, returns_100x8):
        result = systemic_risk_dashboard(returns_100x8)
        assert result["risk_summary"]["n_institutions"] == 8

    def test_raises_too_few_observations(self, rng):
        R = rng.normal(0, 0.02, (20, 4))
        with pytest.raises(ValueError, match="30"):
            systemic_risk_dashboard(R)

    def test_raises_too_few_institutions(self, rng):
        R = rng.normal(0, 0.02, (50, 1))
        with pytest.raises(ValueError, match="2"):
            systemic_risk_dashboard(R)

    def test_raises_wrong_liabilities_shape(self, returns_60x4):
        with pytest.raises(ValueError, match="shape"):
            systemic_risk_dashboard(returns_60x4, liabilities=np.zeros((3, 3)))

    def test_recovery_rates_between_zero_and_one(self, returns_60x4):
        result = systemic_risk_dashboard(returns_60x4)
        rr = result["clearing_result"]["recovery_rates"]
        assert np.all(rr >= 0.0)
        assert np.all(rr <= 1.0 + 1e-6)
