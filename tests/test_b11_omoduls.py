"""Tests for B11 omoduls.

Each omodul ≥10 tests covering: status/error keys, fingerprint, decision_trail,
fingerprint_fields sensitivity, failure→trail written, pillar verification,
routing via omodul.compute_fingerprint_for.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import omodul
from omodul.macro_daily_report import (
    MacroDailyReportConfig,
    MacroDailyReportInput,
    compute_fingerprint_for as fp_macro,
    macro_daily_report,
)
from omodul.lhb_institution_vs_hotmoney_panel import (
    LhbPanelConfig,
    LhbPanelInput,
    compute_fingerprint_for as fp_lhb,
    lhb_institution_vs_hotmoney_panel,
)
from omodul.plan_card_render import (
    PlanCardConfig,
    PlanCardInput,
    compute_fingerprint_for as fp_plan,
    plan_card_render,
)
from omodul.discipline_banner_toast_data import (
    DisciplineBannerConfig,
    DisciplineBannerInput,
    compute_fingerprint_for as fp_banner,
    discipline_banner_toast_data,
)
from omodul.monthly_review_cron_orchestrator import (
    MonthlyReviewConfig,
    MonthlyReviewInput,
    compute_fingerprint_for as fp_monthly,
    monthly_review_cron_orchestrator,
)

# ── shared fixtures ────────────────────────────────────────────────────────────

_DATE = date(2026, 5, 1)
_DATE2 = date(2026, 6, 1)

# TradeRecord fields: seat_name, buy_price, t3_price, stop_loss_pct
_RECORDS = [
    {"seat_name": "中信", "buy_price": 10.0, "t3_price": 10.5, "stop_loss_pct": 5.0},
    {"seat_name": "游资A", "buy_price": 20.0, "t3_price": 19.0, "stop_loss_pct": 5.0},
    {"seat_name": "中信", "buy_price": 15.0, "t3_price": 16.5, "stop_loss_pct": 5.0},
    {"seat_name": "游资B", "buy_price": 8.0, "t3_price": 7.5, "stop_loss_pct": 5.0},
    {"seat_name": "中信", "buy_price": 12.0, "t3_price": 12.8, "stop_loss_pct": 5.0},
]

# SeatTradeInput fields: seat_name, buy_price, t3_price
_SEAT_TRADES = [
    {"seat_name": "中信证券", "buy_price": 10.0, "t3_price": 10.5},
    {"seat_name": "游资A", "buy_price": 20.0, "t3_price": 19.0},
]


def _mock_surprise():
    r = MagicMock()
    r.shock_count = 2
    r.surprises = []
    return r


def _mock_cycle():
    r = MagicMock()
    r.phase = "monetary_easing"
    r.confidence = 0.67
    r.evidence = {}
    return r


def _mock_policy():
    r = MagicMock()
    r.attributed_count = 3
    r.rows = []
    return r


# ══════════════════════════════════════════════════════════════════════════════
# 1. macro_daily_report
# ══════════════════════════════════════════════════════════════════════════════

_MACRO_CFG = MacroDailyReportConfig(trade_date=_DATE)
_MACRO_INP = MacroDailyReportInput()

_MACRO_PATCHES = (
    patch("oskill.macro_surprise_compute.macro_surprise_compute", return_value=_mock_surprise()),
    patch("oskill.macro_cycle_engine_v2.macro_cycle_engine_v2", return_value=_mock_cycle()),
    patch(
        "oskill.policy_sector_attribution.policy_sector_attribution", return_value=_mock_policy()
    ),
    patch("obase.ProviderRegistry", create=True),
)


def _run_macro(cfg=None, inp=None, output_dir=None):
    with (
        patch(
            "oskill.macro_surprise_compute.macro_surprise_compute", return_value=_mock_surprise()
        ),
        patch("oskill.macro_cycle_engine_v2.macro_cycle_engine_v2", return_value=_mock_cycle()),
        patch(
            "oskill.policy_sector_attribution.policy_sector_attribution",
            return_value=_mock_policy(),
        ),
        patch("obase.ProviderRegistry", create=True),
    ):
        return macro_daily_report(cfg or _MACRO_CFG, inp or _MACRO_INP, output_dir)


class TestMacroDailyReport:
    def test_status_key_present(self):
        assert "status" in _run_macro()

    def test_error_key_present(self):
        assert "error" in _run_macro()

    def test_fingerprint_is_64_hex(self):
        r = _run_macro()
        assert len(r["fingerprint"]) == 64

    def test_decision_trail_is_dict(self):
        assert isinstance(_run_macro()["decision_trail"], dict)

    def test_cost_usd_present(self):
        assert "cost_usd" in _run_macro()

    def test_report_path_key_present(self):
        assert "report_path" in _run_macro()

    def test_fingerprint_changes_on_trade_date(self):
        cfg2 = MacroDailyReportConfig(trade_date=_DATE2)
        assert fp_macro(_MACRO_CFG, _MACRO_INP) != fp_macro(cfg2, _MACRO_INP)

    def test_fingerprint_changes_on_report_type(self):
        cfg2 = MacroDailyReportConfig(trade_date=_DATE, report_type="weekly")
        assert fp_macro(_MACRO_CFG, _MACRO_INP) != fp_macro(cfg2, _MACRO_INP)

    def test_fingerprint_stable_on_lookback_months(self):
        cfg2 = MacroDailyReportConfig(trade_date=_DATE, lookback_months=12)
        assert fp_macro(_MACRO_CFG, _MACRO_INP) == fp_macro(cfg2, _MACRO_INP)

    def test_failure_gives_failed_status(self, tmp_path):
        with patch(
            "oskill.macro_surprise_compute.macro_surprise_compute",
            side_effect=RuntimeError("net"),
        ):
            r = macro_daily_report(_MACRO_CFG, _MACRO_INP, tmp_path)
        assert r["status"] == "failed"

    def test_failure_still_writes_trail(self, tmp_path):
        with patch(
            "oskill.macro_surprise_compute.macro_surprise_compute",
            side_effect=RuntimeError("net"),
        ):
            macro_daily_report(_MACRO_CFG, _MACRO_INP, tmp_path)
        assert (tmp_path / "decision_trail.json").exists()

    def test_report_written_with_output_dir(self, tmp_path):
        _run_macro(output_dir=tmp_path)
        assert (tmp_path / "decision_trail.json").exists()

    def test_routing_via_omodul(self):
        fp = omodul.compute_fingerprint_for("macro_daily_report", _MACRO_CFG, _MACRO_INP)
        assert isinstance(fp, str) and len(fp) == 64


# ══════════════════════════════════════════════════════════════════════════════
# 2. lhb_institution_vs_hotmoney_panel
# ══════════════════════════════════════════════════════════════════════════════

_LHB_CFG = LhbPanelConfig(trade_date=_DATE, symbol_scope="all")
_LHB_INP = LhbPanelInput(seat_trades=_SEAT_TRADES)


def _run_lhb(cfg=None, inp=None, output_dir=None):
    with patch("oprim.fetch_sector_returns", return_value=[]):
        return lhb_institution_vs_hotmoney_panel(cfg or _LHB_CFG, inp or _LHB_INP, output_dir)


class TestLhbPanel:
    def test_status_present(self):
        assert "status" in _run_lhb()

    def test_error_present(self):
        assert "error" in _run_lhb()

    def test_fingerprint_64_hex(self):
        assert len(_run_lhb()["fingerprint"]) == 64

    def test_decision_trail_dict(self):
        assert isinstance(_run_lhb()["decision_trail"], dict)

    def test_findings_n_seats(self):
        r = _run_lhb()
        assert r["findings"] is not None
        assert "n_seats_analyzed" in r["findings"]

    def test_findings_audit_summary_keys(self):
        r = _run_lhb()
        assert "total_observed" in r["findings"]["audit_summary"]

    def test_fingerprint_changes_on_trade_date(self):
        cfg2 = LhbPanelConfig(trade_date=_DATE2, symbol_scope="all")
        assert fp_lhb(_LHB_CFG, _LHB_INP) != fp_lhb(cfg2, _LHB_INP)

    def test_fingerprint_changes_on_symbol_scope(self):
        cfg2 = LhbPanelConfig(trade_date=_DATE, symbol_scope="watchlist")
        assert fp_lhb(_LHB_CFG, _LHB_INP) != fp_lhb(cfg2, _LHB_INP)

    def test_fingerprint_stable_on_match_threshold(self):
        cfg2 = LhbPanelConfig(trade_date=_DATE, symbol_scope="all", match_threshold=0.9)
        assert fp_lhb(_LHB_CFG, _LHB_INP) == fp_lhb(cfg2, _LHB_INP)

    def test_failure_gives_failed_status(self, tmp_path):
        # seat_winrate_aggregator is in the outer try block → propagates to status="failed"
        with patch(
            "oskill.seat_winrate_aggregator.seat_winrate_aggregator",
            side_effect=RuntimeError("crash"),
        ):
            r = lhb_institution_vs_hotmoney_panel(_LHB_CFG, _LHB_INP, tmp_path)
        assert r["status"] == "failed"

    def test_failure_writes_trail(self, tmp_path):
        with patch(
            "oskill.seat_winrate_aggregator.seat_winrate_aggregator",
            side_effect=RuntimeError("crash"),
        ):
            lhb_institution_vs_hotmoney_panel(_LHB_CFG, _LHB_INP, tmp_path)
        assert (tmp_path / "decision_trail.json").exists()

    def test_empty_seat_trades_completed(self):
        r = _run_lhb(inp=LhbPanelInput())
        assert r["status"] == "completed"
        assert r["findings"]["n_seats_analyzed"] == 0

    def test_routing_via_omodul(self):
        fp = omodul.compute_fingerprint_for("lhb_institution_vs_hotmoney_panel", _LHB_CFG, _LHB_INP)
        assert isinstance(fp, str)


# ══════════════════════════════════════════════════════════════════════════════
# 3. plan_card_render
# ══════════════════════════════════════════════════════════════════════════════

_PLAN_CFG = PlanCardConfig(symbol="000001", trade_date=_DATE)
_PLAN_INP = PlanCardInput(
    universe=[{"symbol": f"00000{i}", "score": float(i)} for i in range(1, 6)],
)


class TestPlanCardRender:
    def test_status_present(self):
        assert "status" in plan_card_render(_PLAN_CFG, _PLAN_INP)

    def test_error_present(self):
        assert "error" in plan_card_render(_PLAN_CFG, _PLAN_INP)

    def test_fingerprint_64_hex(self):
        assert len(plan_card_render(_PLAN_CFG, _PLAN_INP)["fingerprint"]) == 64

    def test_decision_trail_dict(self):
        assert isinstance(plan_card_render(_PLAN_CFG, _PLAN_INP)["decision_trail"], dict)

    def test_findings_symbol(self):
        r = plan_card_render(_PLAN_CFG, _PLAN_INP)
        assert r["findings"]["symbol"] == "000001"

    def test_findings_total_candidates(self):
        r = plan_card_render(_PLAN_CFG, _PLAN_INP)
        assert r["findings"]["total_candidates"] >= 0

    def test_fingerprint_changes_on_symbol(self):
        cfg2 = PlanCardConfig(symbol="000002", trade_date=_DATE)
        assert fp_plan(_PLAN_CFG, _PLAN_INP) != fp_plan(cfg2, _PLAN_INP)

    def test_fingerprint_changes_on_trade_date(self):
        cfg2 = PlanCardConfig(symbol="000001", trade_date=_DATE2)
        assert fp_plan(_PLAN_CFG, _PLAN_INP) != fp_plan(cfg2, _PLAN_INP)

    def test_fingerprint_stable_on_top_n(self):
        cfg2 = PlanCardConfig(symbol="000001", trade_date=_DATE, top_n=50)
        assert fp_plan(_PLAN_CFG, _PLAN_INP) == fp_plan(cfg2, _PLAN_INP)

    def test_failure_gives_failed_status(self, tmp_path):
        with patch(
            "oskill.candidate_universe_builder_v3.candidate_universe_builder_v3",
            side_effect=RuntimeError("crash"),
        ):
            r = plan_card_render(_PLAN_CFG, _PLAN_INP, tmp_path)
        assert r["status"] == "failed"

    def test_failure_writes_trail(self, tmp_path):
        with patch(
            "oskill.candidate_universe_builder_v3.candidate_universe_builder_v3",
            side_effect=RuntimeError("crash"),
        ):
            plan_card_render(_PLAN_CFG, _PLAN_INP, tmp_path)
        assert (tmp_path / "decision_trail.json").exists()

    def test_empty_universe_completed(self):
        r = plan_card_render(_PLAN_CFG, PlanCardInput())
        assert r["status"] == "completed"
        assert r["findings"]["total_candidates"] == 0

    def test_routing_via_omodul(self):
        fp = omodul.compute_fingerprint_for("plan_card_render", _PLAN_CFG, _PLAN_INP)
        assert isinstance(fp, str)


# ══════════════════════════════════════════════════════════════════════════════
# 4. discipline_banner_toast_data
# ══════════════════════════════════════════════════════════════════════════════

_BAN_CFG = DisciplineBannerConfig(user_id_hash="abc123", trade_date=_DATE)
_BAN_INP = DisciplineBannerInput(trade_records=_RECORDS)


class TestDisciplineBanner:
    def test_status_present(self):
        assert "status" in discipline_banner_toast_data(_BAN_CFG, _BAN_INP)

    def test_error_present(self):
        assert "error" in discipline_banner_toast_data(_BAN_CFG, _BAN_INP)

    def test_fingerprint_64_hex(self):
        assert len(discipline_banner_toast_data(_BAN_CFG, _BAN_INP)["fingerprint"]) == 64

    def test_decision_trail_dict(self):
        assert isinstance(discipline_banner_toast_data(_BAN_CFG, _BAN_INP)["decision_trail"], dict)

    def test_findings_banner_severity_valid(self):
        r = discipline_banner_toast_data(_BAN_CFG, _BAN_INP)
        assert r["findings"]["banner_severity"] in ("green", "yellow", "red")

    def test_findings_toast_message_nonempty(self):
        r = discipline_banner_toast_data(_BAN_CFG, _BAN_INP)
        assert len(r["findings"]["toast_message"]) > 0

    def test_fingerprint_changes_on_user_id_hash(self):
        cfg2 = DisciplineBannerConfig(user_id_hash="xyz999", trade_date=_DATE)
        assert fp_banner(_BAN_CFG, _BAN_INP) != fp_banner(cfg2, _BAN_INP)

    def test_fingerprint_changes_on_trade_date(self):
        cfg2 = DisciplineBannerConfig(user_id_hash="abc123", trade_date=_DATE2)
        assert fp_banner(_BAN_CFG, _BAN_INP) != fp_banner(cfg2, _BAN_INP)

    def test_fingerprint_stable_on_stop_loss_threshold(self):
        cfg2 = DisciplineBannerConfig(
            user_id_hash="abc123", trade_date=_DATE, stop_loss_threshold_pct=-8.0
        )
        assert fp_banner(_BAN_CFG, _BAN_INP) == fp_banner(cfg2, _BAN_INP)

    def test_failure_gives_failed_status(self, tmp_path):
        with patch(
            "oskill.discipline_vs_violation_winrate_compute.discipline_vs_violation_winrate_compute",
            side_effect=RuntimeError("crash"),
        ):
            r = discipline_banner_toast_data(_BAN_CFG, _BAN_INP, tmp_path)
        assert r["status"] == "failed"

    def test_failure_writes_trail(self, tmp_path):
        with patch(
            "oskill.discipline_vs_violation_winrate_compute.discipline_vs_violation_winrate_compute",
            side_effect=RuntimeError("crash"),
        ):
            discipline_banner_toast_data(_BAN_CFG, _BAN_INP, tmp_path)
        assert (tmp_path / "decision_trail.json").exists()

    def test_empty_records_completed(self):
        r = discipline_banner_toast_data(_BAN_CFG, DisciplineBannerInput())
        assert r["status"] == "completed"
        assert r["findings"]["n_compliant"] == 0

    def test_breach_detection_present(self):
        inp = DisciplineBannerInput(
            trade_records=_RECORDS,
            current_open_trades=[
                {
                    "symbol": "000001",
                    "entry_price": 10.0,
                    "current_price": 9.0,
                    "stop_loss_pct": 5.0,
                },
            ],
        )
        r = discipline_banner_toast_data(_BAN_CFG, inp)
        assert "open_trades_breach_count" in r["findings"]

    def test_routing_via_omodul(self):
        fp = omodul.compute_fingerprint_for("discipline_banner_toast_data", _BAN_CFG, _BAN_INP)
        assert isinstance(fp, str)


# ══════════════════════════════════════════════════════════════════════════════
# 5. monthly_review_cron_orchestrator
# ══════════════════════════════════════════════════════════════════════════════

_MR_CFG = MonthlyReviewConfig(user_id_hash="u001", year_month="2026-05")
_MR_INP = MonthlyReviewInput(trade_records=_RECORDS)


def _run_monthly(cfg=None, inp=None, output_dir=None):
    with patch("obase.ProviderRegistry", create=True):
        return monthly_review_cron_orchestrator(cfg or _MR_CFG, inp or _MR_INP, output_dir)


class TestMonthlyReviewCron:
    def test_status_present(self):
        assert "status" in _run_monthly()

    def test_error_present(self):
        assert "error" in _run_monthly()

    def test_fingerprint_64_hex(self):
        assert len(_run_monthly()["fingerprint"]) == 64

    def test_decision_trail_dict(self):
        assert isinstance(_run_monthly()["decision_trail"], dict)

    def test_cost_usd_present(self):
        assert "cost_usd" in _run_monthly()

    def test_report_path_key_present(self):
        assert "report_path" in _run_monthly()

    def test_findings_year_month(self):
        assert _run_monthly()["findings"]["year_month"] == "2026-05"

    def test_fingerprint_changes_on_user_id_hash(self):
        cfg2 = MonthlyReviewConfig(user_id_hash="u002", year_month="2026-05")
        assert fp_monthly(_MR_CFG, _MR_INP) != fp_monthly(cfg2, _MR_INP)

    def test_fingerprint_changes_on_year_month(self):
        cfg2 = MonthlyReviewConfig(user_id_hash="u001", year_month="2026-04")
        assert fp_monthly(_MR_CFG, _MR_INP) != fp_monthly(cfg2, _MR_INP)

    def test_fingerprint_stable_on_template_name(self):
        cfg2 = MonthlyReviewConfig(user_id_hash="u001", year_month="2026-05", template_name="other")
        assert fp_monthly(_MR_CFG, _MR_INP) == fp_monthly(cfg2, _MR_INP)

    def test_failure_gives_failed_status(self, tmp_path):
        with patch(
            "oskill.discipline_vs_violation_winrate_compute.discipline_vs_violation_winrate_compute",
            side_effect=RuntimeError("crash"),
        ):
            r = monthly_review_cron_orchestrator(_MR_CFG, _MR_INP, tmp_path)
        assert r["status"] == "failed"

    def test_failure_writes_trail(self, tmp_path):
        with patch(
            "oskill.discipline_vs_violation_winrate_compute.discipline_vs_violation_winrate_compute",
            side_effect=RuntimeError("crash"),
        ):
            monthly_review_cron_orchestrator(_MR_CFG, _MR_INP, tmp_path)
        assert (tmp_path / "decision_trail.json").exists()

    def test_trail_written_to_output_dir(self, tmp_path):
        _run_monthly(output_dir=tmp_path)
        assert (tmp_path / "decision_trail.json").exists()

    def test_empty_records_completed(self):
        r = _run_monthly(inp=MonthlyReviewInput())
        assert r["status"] == "completed"
        assert r["findings"]["n_trades"] == 0

    def test_routing_via_omodul(self):
        fp = omodul.compute_fingerprint_for("monthly_review_cron_orchestrator", _MR_CFG, _MR_INP)
        assert isinstance(fp, str)
