"""Tests for omodul.execution_models."""
from __future__ import annotations

import pytest

from omodul.execution_models import twap_with_impact, aggressive_limit

_COST_MODEL = "crypto_market_impact_sigmoid"
_COST_PARAMS = {}  # use defaults


class TestTwapWithImpact:
    def test_twap_basic(self):
        result = twap_with_impact(
            target_notional_usd=100_000.0,
            daily_volume_usd=1e8,
            realized_vol_30d=0.5,
            slice_duration_sec=60,
            n_slices=5,
            cost_model_name=_COST_MODEL,
            cost_model_params=_COST_PARAMS,
        )
        assert len(result["schedule"]) == 5

    def test_twap_schedule_structure(self):
        result = twap_with_impact(
            target_notional_usd=50_000.0,
            daily_volume_usd=5e7,
            realized_vol_30d=0.6,
            slice_duration_sec=30,
            n_slices=3,
            cost_model_name=_COST_MODEL,
            cost_model_params=_COST_PARAMS,
        )
        for i, entry in enumerate(result["schedule"]):
            assert entry["slice_index"] == i
            assert entry["offset_sec"] == i * 30
            assert entry["notional_usd"] == pytest.approx(50_000.0 / 3, rel=1e-6)
            assert "expected_impact_bps" in entry

    def test_twap_total_impact_positive(self):
        result = twap_with_impact(
            target_notional_usd=100_000.0,
            daily_volume_usd=1e8,
            realized_vol_30d=0.5,
            slice_duration_sec=60,
            n_slices=5,
            cost_model_name=_COST_MODEL,
            cost_model_params=_COST_PARAMS,
        )
        assert result["total_expected_impact_bps"] > 0
        assert result["total_slippage_estimate_usd"] > 0

    def test_twap_output_keys(self):
        result = twap_with_impact(
            target_notional_usd=10_000.0,
            daily_volume_usd=1e7,
            realized_vol_30d=0.4,
            slice_duration_sec=60,
            n_slices=2,
            cost_model_name=_COST_MODEL,
            cost_model_params=_COST_PARAMS,
        )
        assert set(result.keys()) == {
            "schedule", "total_expected_impact_bps",
            "total_slippage_estimate_usd", "urgency",
        }

    def test_twap_urgency_stored(self):
        result = twap_with_impact(
            target_notional_usd=10_000.0,
            daily_volume_usd=1e7,
            realized_vol_30d=0.4,
            slice_duration_sec=60,
            n_slices=2,
            cost_model_name=_COST_MODEL,
            cost_model_params=_COST_PARAMS,
            urgency="high",
        )
        assert result["urgency"] == "high"

    def test_twap_wrong_cost_model_raises(self):
        with pytest.raises(ValueError, match="cost_model_name"):
            twap_with_impact(
                target_notional_usd=100_000.0,
                daily_volume_usd=1e8,
                realized_vol_30d=0.5,
                slice_duration_sec=60,
                n_slices=5,
                cost_model_name="square_root_law",
                cost_model_params={},
            )

    def test_twap_n_slices_zero_raises(self):
        with pytest.raises(ValueError, match="n_slices"):
            twap_with_impact(
                target_notional_usd=100_000.0,
                daily_volume_usd=1e8,
                realized_vol_30d=0.5,
                slice_duration_sec=60,
                n_slices=0,
                cost_model_name=_COST_MODEL,
                cost_model_params={},
            )

    def test_twap_negative_notional_raises(self):
        with pytest.raises(ValueError, match="target_notional"):
            twap_with_impact(
                target_notional_usd=-100.0,
                daily_volume_usd=1e8,
                realized_vol_30d=0.5,
                slice_duration_sec=60,
                n_slices=5,
                cost_model_name=_COST_MODEL,
                cost_model_params={},
            )


class TestAggressiveLimit:
    def test_aggressive_limit_basic(self):
        """Small order vs large volume → execute=True."""
        result = aggressive_limit(
            target_notional_usd=10_000.0,
            cost_model_name=_COST_MODEL,
            cost_model_params={
                "daily_volume_usd": 1e9,
                "realized_vol_30d": 0.5,
            },
            limit_offset_bps=5,
            timeout_sec=30,
            on_timeout="cancel",
            max_slippage_bps=100,
        )
        assert result["execute"] is True
        assert result["estimated_impact_bps"] > 0

    def test_aggressive_limit_cancel_if_expensive(self):
        """Very low max_slippage_bps → execute=False."""
        result = aggressive_limit(
            target_notional_usd=1_000_000.0,
            cost_model_name=_COST_MODEL,
            cost_model_params={
                "daily_volume_usd": 1_000_000.0,  # same as order = 100% ADV
                "realized_vol_30d": 1.0,
            },
            limit_offset_bps=5,
            timeout_sec=30,
            on_timeout="market",
            max_slippage_bps=1,  # 1 bps max → almost certainly exceeded
        )
        assert result["execute"] is False

    def test_aggressive_limit_output_keys(self):
        result = aggressive_limit(
            target_notional_usd=10_000.0,
            cost_model_name=_COST_MODEL,
            cost_model_params={"daily_volume_usd": 1e8, "realized_vol_30d": 0.5},
            limit_offset_bps=5,
            timeout_sec=30,
            on_timeout="cancel",
            max_slippage_bps=50,
        )
        assert set(result.keys()) == {
            "limit_offset_bps", "timeout_sec", "on_timeout",
            "max_slippage_bps", "estimated_impact_bps", "execute",
        }

    def test_aggressive_limit_wrong_cost_model_raises(self):
        with pytest.raises(ValueError, match="cost_model_name"):
            aggressive_limit(
                target_notional_usd=10_000.0,
                cost_model_name="linear_model",
                cost_model_params={"daily_volume_usd": 1e8, "realized_vol_30d": 0.5},
                limit_offset_bps=5,
                timeout_sec=30,
                on_timeout="cancel",
                max_slippage_bps=50,
            )

    def test_aggressive_limit_invalid_on_timeout_raises(self):
        with pytest.raises(ValueError, match="on_timeout"):
            aggressive_limit(
                target_notional_usd=10_000.0,
                cost_model_name=_COST_MODEL,
                cost_model_params={"daily_volume_usd": 1e8, "realized_vol_30d": 0.5},
                limit_offset_bps=5,
                timeout_sec=30,
                on_timeout="ignore",
                max_slippage_bps=50,
            )

    def test_aggressive_limit_zero_max_slippage_raises(self):
        with pytest.raises(ValueError, match="max_slippage_bps"):
            aggressive_limit(
                target_notional_usd=10_000.0,
                cost_model_name=_COST_MODEL,
                cost_model_params={"daily_volume_usd": 1e8, "realized_vol_30d": 0.5},
                limit_offset_bps=5,
                timeout_sec=30,
                on_timeout="cancel",
                max_slippage_bps=0,
            )
