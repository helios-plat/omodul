"""WAVE D gates: omodul.optimize_skill transaction.

SKILL_BASELINE / CANDIDATE_GENERATION / HELD_OUT_VALIDATION /
VALIDATION_LEAKAGE=0 / SELECTION / SOURCE_MUTATION=0 / AUTO_PROMOTION=0 /
OMODUL_SIGNATURE / PILLARS / SIBLING_CALLS=0.
"""

from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pytest

from omodul.optimize_skill import Config, InputData, compute_fingerprint_for, optimize_skill


def _evaluator(skill, cases):
    # score = skill level (0..1); error_rate inverse
    level = float(skill.get("level", 0.0))
    return {"score": level, "error_rate": 1.0 - level}


def _generator(skill, objective, iteration):
    nxt = dict(skill)
    nxt["level"] = min(1.0, float(skill.get("level", 0.0)) + 0.3)
    nxt["rev"] = iteration
    return nxt


DIRECTIONS = {"score": "higher_is_better", "error_rate": "lower_is_better"}


def _config(**overrides):
    base = dict(metric_directions=DIRECTIONS)
    base.update(overrides)
    return Config(**base)


def _input(**overrides):
    base = dict(
        skill={"name": "demo-skill", "level": 0.2},
        training_cases=[{"q": 1}, {"q": 2}],
        validation_cases=[{"q": 9}],
        candidate_generator=_generator,
        evaluator=_evaluator,
    )
    base.update(overrides)
    return InputData(**base)


def test_signature_and_pillars():
    sig = inspect.signature(optimize_skill)
    assert list(sig.parameters) == ["config", "input_data", "output_dir"]
    assert Config._omodul_name == "optimize_skill"
    assert {"fingerprint", "cost"} <= set(Config._omodul_version and Config._enabled_pillars)
    assert callable(compute_fingerprint_for)


def test_baseline_candidates_selection(tmp_path):
    out = optimize_skill(_config(max_iterations=2), _input(), tmp_path)
    assert out["status"] == "completed"
    assert out["baseline_metrics"] == {"score": 0.2, "error_rate": 0.8}
    assert len(out["candidate_metrics"]) == 2
    assert out["candidate_metrics"][0]["regressed"] is False
    assert out["selected_candidate"]["level"] == pytest.approx(0.8)
    assert out["validation_metrics"]["selected"]["score"] == pytest.approx(0.8)
    assert out["validation_metrics"]["selected"]["error_rate"] == pytest.approx(0.2)
    assert out["validation_leakage"] is False
    assert len(out["artifacts"]) == 3
    for artifact in out["artifacts"]:
        assert Path(artifact).exists()


def test_held_out_not_used_for_selection(tmp_path):
    seen_valid = []

    def spy_evaluator(skill, cases):
        if any(c == {"q": 9} for c in cases):
            seen_valid.append(dict(skill))
        return _evaluator(skill, cases)

    inp = _input(evaluator=spy_evaluator)
    out = optimize_skill(_config(max_iterations=2), inp, tmp_path)
    assert out["status"] == "completed"
    # validation 只跑 selected + baseline 两次
    assert len(seen_valid) == 2
    names = [s.get("level") for s in seen_valid]
    assert 0.2 in names  # baseline
    assert out["selected_candidate"]["level"] in names


def test_validation_leakage_rejected(tmp_path):
    inp = _input(validation_cases=[{"q": 1}])
    out = optimize_skill(_config(), inp, tmp_path)
    assert out["status"] == "failed"
    assert out["validation_leakage"] is True


def test_source_skill_not_mutated(tmp_path):
    skill = {"name": "demo-skill", "level": 0.2, "nested": {"a": [1, 2]}}
    snapshot = copy.deepcopy(skill)
    inp = _input(skill=skill)
    out = optimize_skill(_config(max_iterations=2), inp, tmp_path)
    assert out["status"] == "completed"
    assert inp.skill == snapshot


def test_regressed_candidates_not_selected(tmp_path):
    def bad_generator(skill, objective, iteration):
        nxt = dict(skill)
        nxt["level"] = max(0.0, float(skill.get("level", 0.0)) - 0.1)
        return nxt

    inp = _input(candidate_generator=bad_generator)
    out = optimize_skill(_config(max_iterations=2), inp, tmp_path)
    assert out["status"] == "completed"
    assert all(c["regressed"] for c in out["candidate_metrics"])
    assert out["selected_candidate"] is None
    assert out["validation_metrics"] == {}


def test_no_auto_promotion(tmp_path):
    src = Path(__file__).parent.parent.joinpath("omodul/optimize_skill.py").read_text()
    for forbidden in ("SkillsRegistry", "skills_registry", "activate(", "promote("):
        assert forbidden not in src


def test_no_sibling_calls(tmp_path):
    src = Path(__file__).parent.parent.joinpath("omodul/optimize_skill.py").read_text()
    # 仅允许 _base 支柱 infra;不得调用 sibling omodul 元素
    assert "from omodul._base import" in src
    lines = [ln for ln in src.splitlines() if "from omodul." in ln or "import omodul." in ln]
    assert all("_base" in ln for ln in lines), lines


def test_artifacts_do_not_overwrite_source(tmp_path):
    skill_path = tmp_path / "skill.json"
    skill_path.write_text(json.dumps({"name": "demo-skill", "level": 0.2}))
    out = optimize_skill(_config(max_iterations=1), _input(), tmp_path)
    assert out["status"] == "completed"
    assert json.loads(skill_path.read_text()) == {"name": "demo-skill", "level": 0.2}
