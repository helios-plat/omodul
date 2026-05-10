"""Tests for Group 4: Signal & Alert modules."""

import numpy as np
import pandas as pd
import pytest

from omodul.signals import alert_calibration_engine, thesis_invalidation_monitor


class TestAlertCalibrationEngine:
    def test_basic_calibration(self):
        rng = np.random.default_rng(42)
        n = 200
        df = pd.DataFrame({
            "alert_type": rng.choice(["price", "volume"], n),
            "predicted_prob": rng.uniform(0, 1, n),
            "actual_outcome": rng.choice([0.0, 1.0], n),
        })
        result = alert_calibration_engine(df)
        assert "overall" in result
        assert "per_group" in result
        assert result["summary"]["n_alerts_total"] == n

    def test_with_bandit_state(self):
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "alert_type": ["A"] * 100,
            "predicted_prob": rng.uniform(0, 1, 100),
            "actual_outcome": rng.choice([0.0, 1.0], 100),
        })
        result = alert_calibration_engine(df, include_bandit_state=True)
        assert result["per_group"]["A"]["bandit_state"] is not None

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            alert_calibration_engine(pd.DataFrame(columns=["predicted_prob", "actual_outcome"]))

    def test_missing_columns_raises(self):
        with pytest.raises(ValueError, match="columns"):
            alert_calibration_engine(pd.DataFrame({"x": [1]}))


class TestThesisInvalidationMonitor:
    def test_basic_monitoring(self):
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "thesis_id": ["T1"] * 50 + ["T2"] * 50,
            "predicted_prob": rng.uniform(0.3, 0.7, 100),
            "actual_outcome": rng.choice([0.0, 1.0], 100),
        })
        result = thesis_invalidation_monitor(df, rolling_window=20)
        assert "per_thesis" in result
        assert "summary" in result
        assert result["summary"]["n_thesis"] == 2

    def test_invalidated_thesis(self):
        # Create a thesis with terrible predictions
        df = pd.DataFrame({
            "thesis_id": ["BAD"] * 50,
            "predicted_prob": np.ones(50) * 0.9,  # always predicts 0.9
            "actual_outcome": np.zeros(50),  # always wrong
        })
        result = thesis_invalidation_monitor(df, rolling_window=20, brier_threshold=0.25)
        assert result["per_thesis"]["BAD"]["status"] in ("AT_RISK", "INVALIDATED")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            thesis_invalidation_monitor(pd.DataFrame(columns=["thesis_id", "predicted_prob", "actual_outcome"]))

    def test_missing_group_by_raises(self):
        with pytest.raises(ValueError, match="group_by"):
            thesis_invalidation_monitor(
                pd.DataFrame({"predicted_prob": [0.5], "actual_outcome": [1.0]}),
                group_by="missing_col",
            )
