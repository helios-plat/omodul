"""Tests for epstein_zin_asset_pricing_workflow."""
from __future__ import annotations

import numpy as np
import pytest

from omodul.asset_pricing.ez_workflow import epstein_zin_asset_pricing_workflow


class TestEpsteinZinWorkflow:
    def test_basic_output_keys(self):
        result = epstein_zin_asset_pricing_workflow()
        assert set(result.keys()) == {
            "value_function", "equity_premium", "risk_free_rate", "aggregator_check"
        }

    def test_value_function_is_array(self):
        result = epstein_zin_asset_pricing_workflow()
        assert isinstance(result["value_function"], np.ndarray)
        assert len(result["value_function"]) > 0

    def test_value_function_positive(self):
        result = epstein_zin_asset_pricing_workflow()
        assert np.all(result["value_function"] > 0)

    def test_equity_premium_non_negative(self):
        """Standard BY calibration (gamma > 1/ies = 10 > 0.67) should give positive EP."""
        result = epstein_zin_asset_pricing_workflow(risk_aversion=10.0, ies=1.5)
        assert result["equity_premium"] >= 0.0

    def test_equity_premium_is_float(self):
        result = epstein_zin_asset_pricing_workflow()
        assert isinstance(result["equity_premium"], float)

    def test_risk_free_rate_is_float(self):
        result = epstein_zin_asset_pricing_workflow()
        assert isinstance(result["risk_free_rate"], float)

    def test_aggregator_check_positive(self):
        result = epstein_zin_asset_pricing_workflow()
        assert result["aggregator_check"] > 0

    def test_higher_ra_higher_ep(self):
        """Increasing risk aversion (above 1/ies) increases equity premium."""
        r1 = epstein_zin_asset_pricing_workflow(risk_aversion=5.0, ies=1.5)
        r2 = epstein_zin_asset_pricing_workflow(risk_aversion=15.0, ies=1.5)
        assert r2["equity_premium"] >= r1["equity_premium"]

    def test_default_params_run(self):
        """Default params should complete without error."""
        result = epstein_zin_asset_pricing_workflow()
        assert np.isfinite(result["equity_premium"])

    def test_raises_invalid_risk_aversion(self):
        with pytest.raises(ValueError, match="risk_aversion"):
            epstein_zin_asset_pricing_workflow(risk_aversion=0.0)

    def test_raises_invalid_ies(self):
        with pytest.raises(ValueError, match="ies"):
            epstein_zin_asset_pricing_workflow(ies=0.0)

    def test_raises_invalid_discount(self):
        with pytest.raises(ValueError, match="discount"):
            epstein_zin_asset_pricing_workflow(discount=1.1)

    def test_raises_negative_discount(self):
        with pytest.raises(ValueError, match="discount"):
            epstein_zin_asset_pricing_workflow(discount=-0.1)

    def test_value_function_shape_consistent(self):
        result = epstein_zin_asset_pricing_workflow()
        vf = result["value_function"]
        assert vf.ndim == 1
        assert len(vf) >= 10
