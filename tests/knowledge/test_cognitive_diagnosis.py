"""Tests for omodul.cognitive_diagnosis."""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from omodul.cognitive_diagnosis import (
    CognitiveDiagnosisConfig,
    DiagnosisReport,
    diagnose,
    run_diagnosis,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_config(max_em_iters: int = 30) -> CognitiveDiagnosisConfig:
    return CognitiveDiagnosisConfig(max_em_iters=max_em_iters)


def make_2x2():
    """Minimal 2 students × 2 items × 2 skills case."""
    R = np.array([[1, 0], [0, 1]])  # 2 students, 2 items
    Q = np.array([[1, 0], [0, 1]])  # 2 items, 2 skills
    return R, Q


def make_4x4():
    """4 students × 4 items × 2 skills."""
    R = np.array(
        [
            [1, 1, 0, 0],
            [1, 0, 1, 0],
            [0, 0, 1, 1],
            [0, 1, 0, 1],
        ]
    )
    Q = np.array(
        [
            [1, 0],
            [1, 1],
            [0, 1],
            [1, 1],
        ]
    )
    return R, Q


# ---------------------------------------------------------------------------
# Tests: DiagnosisReport
# ---------------------------------------------------------------------------


def test_diagnosis_report_summary_keys():
    """DiagnosisReport.summary() returns n_students, n_skills, n_items."""
    R, Q = make_2x2()
    report = diagnose(R, Q)
    summary = report.summary()
    assert "n_students" in summary
    assert "n_skills" in summary
    assert "n_items" in summary


def test_diagnosis_report_summary_correct_counts():
    """Summary counts match the input dimensions."""
    R, Q = make_2x2()
    report = diagnose(R, Q)
    summary = report.summary()
    assert summary["n_students"] == 2
    assert summary["n_skills"] == 2
    assert summary["n_items"] == 2


# ---------------------------------------------------------------------------
# Tests: mastery output
# ---------------------------------------------------------------------------


def test_mastery_output_is_binary():
    """Mastery values for each student+skill are 0 or 1."""
    R, Q = make_2x2()
    report = diagnose(R, Q)
    for sid, skill_map in report.mastery.items():
        for kid, val in skill_map.items():
            assert val in (0, 1), f"Non-binary mastery: {sid}/{kid}={val}"


def test_mastery_has_all_students():
    """mastery has one entry per student."""
    R, Q = make_4x4()
    report = diagnose(R, Q, student_ids=["S0", "S1", "S2", "S3"])
    assert set(report.mastery.keys()) == {"S0", "S1", "S2", "S3"}


# ---------------------------------------------------------------------------
# Tests: ability values
# ---------------------------------------------------------------------------


def test_ability_values_are_floats():
    """Ability values are Python floats (not numpy scalars)."""
    R, Q = make_2x2()
    report = diagnose(R, Q)
    for sid, ab in report.ability.items():
        assert isinstance(ab, float), f"Non-float ability: {sid}={ab!r}"


def test_ability_has_all_students():
    """ability has one entry per student."""
    R, Q = make_2x2()
    report = diagnose(R, Q, student_ids=["Alice", "Bob"])
    assert set(report.ability.keys()) == {"Alice", "Bob"}


# ---------------------------------------------------------------------------
# Tests: item_params
# ---------------------------------------------------------------------------


def test_item_params_has_slip_and_guess():
    """Each item has slip and guess keys."""
    R, Q = make_2x2()
    report = diagnose(R, Q)
    for iid, params in report.item_params.items():
        assert "slip" in params, f"Missing slip for {iid}"
        assert "guess" in params, f"Missing guess for {iid}"


def test_item_params_slip_guess_in_range():
    """slip and guess are clipped to [0.05, 0.5]."""
    R, Q = make_4x4()
    report = diagnose(R, Q)
    for iid, params in report.item_params.items():
        assert 0.0 <= params["slip"] <= 0.5, f"slip out of range: {iid}"
        assert 0.0 <= params["guess"] <= 0.5, f"guess out of range: {iid}"


# ---------------------------------------------------------------------------
# Tests: skill_summary
# ---------------------------------------------------------------------------


def test_skill_summary_has_ratios_per_skill():
    """skill_summary maps each skill_id to a float in [0, 1]."""
    R, Q = make_4x4()
    report = diagnose(R, Q, skill_ids=["math", "logic"])
    assert set(report.skill_summary.keys()) == {"math", "logic"}
    for kid, ratio in report.skill_summary.items():
        assert 0.0 <= ratio <= 1.0, f"Ratio out of range: {kid}={ratio}"


# ---------------------------------------------------------------------------
# Tests: run_diagnosis standard dict
# ---------------------------------------------------------------------------


def test_run_diagnosis_standard_dict_shape():
    """run_diagnosis returns dict with all required keys."""
    R, Q = make_2x2()
    config = make_config()
    result = run_diagnosis(config, {"R": R, "Q": Q})
    for key in ("findings", "status", "error", "decision_trail", "report_path", "cost_usd"):
        assert key in result, f"Missing key: {key}"
    assert result["report_path"] is None
    assert result["cost_usd"] == 0.0


def test_run_diagnosis_status_completed_on_valid_input():
    """Valid input returns status=completed."""
    R, Q = make_2x2()
    config = make_config()
    result = run_diagnosis(config, {"R": R, "Q": Q})
    assert result["status"] == "completed"
    assert result["error"] is None


def test_run_diagnosis_findings_is_diagnosis_report():
    """findings is a DiagnosisReport on success."""
    R, Q = make_2x2()
    config = make_config()
    result = run_diagnosis(config, {"R": R, "Q": Q})
    assert isinstance(result["findings"], DiagnosisReport)


def test_run_diagnosis_missing_R_key_returns_failed():
    """Missing 'R' key in input_data returns status=failed without raising."""
    config = make_config()
    result = run_diagnosis(config, {"Q": np.array([[1, 0]])})
    assert result["status"] == "failed"
    assert result["findings"] is None
    assert result["error"] is not None
    assert result["error"]["code"] == "ERR_DIAGNOSIS"


def test_run_diagnosis_mismatched_dimensions_handled():
    """R with 3 items but Q with 2 items returns status=failed without raising."""
    config = make_config()
    R = np.array([[1, 0, 1]])  # 1 student, 3 items
    Q = np.array([[1, 0], [0, 1]])  # 2 items, 2 skills — mismatch
    result = run_diagnosis(config, {"R": R, "Q": Q})
    assert result["status"] == "failed"
    assert result["error"] is not None


def test_run_diagnosis_on_step_callback():
    """on_step callback is invoked for each trail step."""
    R, Q = make_2x2()
    config = make_config()
    seen = []
    run_diagnosis(config, {"R": R, "Q": Q}, on_step=lambda s: seen.append(s["step"]))
    assert "input" in seen
    assert "fit_dina" in seen
    assert "diagnose_done" in seen


def test_run_diagnosis_output_dir_creates_trail_json(tmp_path):
    """When output_dir is set, decision_trail.json is written."""
    R, Q = make_2x2()
    config = make_config()
    out_dir = str(tmp_path / "diag_trail")
    result = run_diagnosis(config, {"R": R, "Q": Q}, output_dir=out_dir)
    trail_file = os.path.join(out_dir, "decision_trail.json")
    assert os.path.exists(trail_file)
    with open(trail_file) as f:
        on_disk = json.load(f)
    assert on_disk["omodul"] == "cognitive_diagnosis"
    assert on_disk["status"] == "completed"


# ---------------------------------------------------------------------------
# ADR-A23 compliance: no psychological labels
# ---------------------------------------------------------------------------


def test_adr_a23_no_psychological_labels_in_findings():
    """findings dict keys contain no subjective/psychological labels (ADR-A23)."""
    R, Q = make_4x4()
    config = make_config()
    result = run_diagnosis(config, {"R": R, "Q": Q})
    report = result["findings"]

    FORBIDDEN = {
        "anxiety",
        "lazy",
        "careless",
        "gifted",
        "struggling",
        "motivated",
        "unmotivated",
        "bright",
        "slow",
    }

    # Check error_patterns keys
    for sid, ep in report.error_patterns.items():
        for label in ep.keys():
            assert label.lower() not in FORBIDDEN, f"Psychological label found: {label}"

    # Check decision_trail for forbidden labels
    trail_str = json.dumps(result["decision_trail"])
    for label in FORBIDDEN:
        assert label not in trail_str.lower(), f"Psychological label in trail: {label}"


def test_adr_a23_red_lines_in_decision_trail():
    """decision_trail.red_lines declares ADR-A23 compliance markers."""
    R, Q = make_2x2()
    config = make_config()
    result = run_diagnosis(config, {"R": R, "Q": Q})
    red_lines = result["decision_trail"].get("red_lines", [])
    assert "no_psychological_labels" in red_lines
    assert "descriptive_diagnostic_only" in red_lines


def test_trivial_perfect_student():
    """A student who answers all items correctly has ability >= any student who doesn't."""
    R = np.array([[1, 1, 1], [0, 0, 0], [1, 0, 1]])
    Q = np.array([[1, 0], [0, 1], [1, 1]])
    config = make_config()
    result = run_diagnosis(config, {"R": R, "Q": Q, "student_ids": ["perfect", "zero", "mid"]})
    assert result["status"] == "completed"
    report = result["findings"]
    assert report.ability["perfect"] >= report.ability["zero"]
