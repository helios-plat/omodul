"""Evaluate a skill version without promotion or registry mutation."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, ClassVar

from oskill.skill_qualification import compare_skill_runs, detect_skill_regression

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint, write_report


class SkillEvaluationConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "evaluate_skill_version"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}


async def _invoke(executor: Any, dataset: Any) -> Any:
    value = executor(dataset) if callable(executor) else executor
    return await value if inspect.isawaitable(value) else value


def _error(kind: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "message": message, **extra}


async def evaluate_skill_version(
    config,
    input_data,
    output_dir,
    *,
    on_step=None,
) -> dict:
    """Run/evaluate isolated baseline and candidate evidence.

    ``input_data`` may provide ``baseline_executor`` and
    ``candidate_executor`` callables.  Without them, ``baseline`` and
    ``candidate`` are treated as already-produced evidence records.
    """
    config = (
        config
        if isinstance(config, SkillEvaluationConfig)
        else SkillEvaluationConfig.model_validate(config or {})
    )
    data = input_data if isinstance(input_data, dict) else dict(input_data)
    trail = Trail()
    fingerprint = compute_fingerprint(
        {"config": config.model_dump(), "input": data.get("dataset", data)}
    )
    empty = {
        "status": "failed",
        "qualified": False,
        "baseline": {},
        "candidate": {},
        "comparison": {},
        "regressions": [],
        "evidence": {},
    }
    dataset = data.get("dataset", [])
    if not dataset:
        return build_result(
            **empty,
            fingerprint=fingerprint,
            trail=trail,
            error=_error("evaluation_failed", "evaluation dataset is empty"),
        )
    try:
        trail.record(event="baseline_start")
        baseline = await _invoke(data.get("baseline_executor"), dataset)
        trail.record(event="candidate_start")
        candidate = await _invoke(data.get("candidate_executor"), dataset)
    except Exception as exc:
        return build_result(
            **empty,
            fingerprint=fingerprint,
            trail=trail,
            error=_error("execution_failed", str(exc), exception=type(exc).__name__),
        )
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        return build_result(
            **empty,
            fingerprint=fingerprint,
            trail=trail,
            error=_error("evaluation_failed", "executors must return evidence dictionaries"),
        )

    comparison = compare_skill_runs(
        baseline=baseline,
        candidate=candidate,
        dimensions=data.get("dimensions"),
        thresholds=data.get("thresholds"),
    )
    if comparison.get("evidence", {}).get("missing_metrics"):
        return build_result(
            status="failed",
            error=_error("evaluation_failed", "required metrics are missing"),
            qualified=False,
            baseline=baseline,
            candidate=candidate,
            comparison=comparison,
            regressions=[],
            evidence=comparison["evidence"],
            fingerprint=fingerprint,
            trail=trail,
        )
    if comparison.get("evidence", {}).get("ambiguous"):
        return build_result(
            status="failed",
            error=_error("evaluation_failed", "evaluation evidence is ambiguous"),
            qualified=False,
            baseline=baseline,
            candidate=candidate,
            comparison=comparison,
            regressions=[],
            evidence=comparison["evidence"],
            fingerprint=fingerprint,
            trail=trail,
        )

    assertion = data.get("assertions")
    if assertion is not None:
        try:
            assertion_result = (
                assertion(baseline, candidate) if callable(assertion) else bool(assertion)
            )
            if inspect.isawaitable(assertion_result):
                assertion_result = await assertion_result
        except Exception as exc:
            return build_result(
                status="failed",
                error=_error("evaluation_failed", str(exc), exception=type(exc).__name__),
                qualified=False,
                baseline=baseline,
                candidate=candidate,
                comparison=comparison,
                regressions=[],
                evidence={"assertion": "error"},
                fingerprint=fingerprint,
                trail=trail,
            )
        if not assertion_result:
            return build_result(
                status="failed",
                error=_error("evaluation_failed", "deterministic assertion failed"),
                qualified=False,
                baseline=baseline,
                candidate=candidate,
                comparison=comparison,
                regressions=[],
                evidence={"assertion": "failed"},
                fingerprint=fingerprint,
                trail=trail,
            )

    regressions = detect_skill_regression(
        comparison=comparison, regression_rules=data.get("regression_rules")
    )
    qualified = not regressions["regressed"]
    status = "completed" if qualified else "failed"
    error = (
        None if qualified else _error("regression_detected", "candidate regressed against baseline")
    )
    evidence = {
        "baseline_candidate_isolated": True,
        "cost_data_present": "cost" in baseline and "cost" in candidate,
    }
    trail.record(event="qualified" if qualified else "regression_detected", qualified=qualified)
    if on_step:
        on_step({"status": status, "qualified": qualified})
    report_path = None
    if output_dir and "report" in config._enabled_pillars:
        report_path = write_report(
            str({"comparison": comparison, "regressions": regressions}),
            output_dir=Path(output_dir),
            name="skill_evaluation",
        )
    return build_result(
        status=status,
        error=error,
        qualified=qualified,
        baseline=baseline,
        candidate=candidate,
        comparison=comparison,
        regressions=regressions["regressions"],
        evidence=evidence,
        fingerprint=fingerprint,
        trail=trail,
        report_path=report_path,
    )


__all__ = ["SkillEvaluationConfig", "evaluate_skill_version"]
