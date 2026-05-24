"""Tests for omodul.macro_daily_report_workflow (C2)."""

from unittest.mock import MagicMock

from omodul.macro_daily_report_workflow import (
    MacroDailyReportConfig,
    compute_fingerprint_for,
    macro_daily_report_workflow,
)


class TestMacroDailyReportWorkflow:
    def test_4_quadrants_full(self) -> None:
        config = MacroDailyReportConfig(report_date="2026-05-25")
        llm = MagicMock()
        llm.call.return_value = "流动性充裕,市场资金面宽松。"
        result = macro_daily_report_workflow(config, llm=llm)
        assert len(result["findings"]["quadrants"]) == 4
        assert result["status"] == "completed"

    def test_single_quadrant_disabled(self) -> None:
        config = MacroDailyReportConfig(report_date="2026-05-25", quadrants=["liquidity"])
        llm = MagicMock()
        llm.call.return_value = "OK"
        result = macro_daily_report_workflow(config, llm=llm)
        assert len(result["findings"]["quadrants"]) == 1

    def test_snapshot_hash_change_fp_change(self) -> None:
        c1 = MacroDailyReportConfig(report_date="2026-05-25", data_snapshot_hash="abc")
        c2 = MacroDailyReportConfig(report_date="2026-05-25", data_snapshot_hash="def")
        assert compute_fingerprint_for(c1) != compute_fingerprint_for(c2)

    def test_same_day_rerun_same_fp_dedup(self) -> None:
        c1 = MacroDailyReportConfig(report_date="2026-05-25", data_snapshot_hash="same")
        c2 = MacroDailyReportConfig(report_date="2026-05-25", data_snapshot_hash="same")
        assert compute_fingerprint_for(c1) == compute_fingerprint_for(c2)

    def test_decision_trail_7_steps(self) -> None:
        config = MacroDailyReportConfig(report_date="2026-05-25")
        llm = MagicMock()
        llm.call.return_value = "Summary"
        result = macro_daily_report_workflow(config, llm=llm)
        assert len(result["decision_trail"]) == 7

    def test_cost_accumulates(self) -> None:
        config = MacroDailyReportConfig(report_date="2026-05-25")
        llm = MagicMock()
        llm.call.return_value = "OK"
        result = macro_daily_report_workflow(config, llm=llm)
        assert result["cost_usd"] > 0

    def test_no_llm_skipped(self) -> None:
        config = MacroDailyReportConfig(report_date="2026-05-25")
        result = macro_daily_report_workflow(config, llm=None)
        assert result["status"] == "completed"
        trail_steps = [t["step"] for t in result["decision_trail"]]
        assert "llm_summary" in trail_steps

    def test_compute_fingerprint_for_public(self) -> None:
        config = MacroDailyReportConfig(report_date="2026-05-25")
        fp = compute_fingerprint_for(config)
        assert isinstance(fp, str)
        assert len(fp) == 16
