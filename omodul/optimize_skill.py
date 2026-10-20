"""
omodul.optimize_skill — Skill 优化事务 (WAVE D 新增元素).

事务: baseline → generate candidates → evaluate → held-out validation
→ compare → select qualified candidate → return artifact/result.

标准签名: (config, input_data, output_dir) -> dict.
复用 oskill.metric_baseline_compare;回归检测与版本评估为本模块私有
helper (非新 canonical 元素),不写 production registry、不 activate、
不 promote、不修改源 skill.

Pillars: fingerprint, cost
"""

from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from omodul._base import BaseConfig, CostTracker, build_result, compute_fingerprint

try:  # 复用 oskill 基线对比 (canonical owner: oskill)
    from oskill.metric_baseline_compare import metric_baseline_compare
except Exception:  # pragma: no cover - 孤立环境回退
    metric_baseline_compare = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Config / InputData
# ---------------------------------------------------------------------------


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "optimize_skill"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"skill", "objective"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "cost"}

    objective: str = "maximize score"
    max_iterations: int = 3
    metric_directions: dict[str, str] = Field(default_factory=dict)


class InputData(BaseModel):
    skill: dict[str, Any]
    training_cases: list[Any]
    validation_cases: list[Any]
    candidate_generator: Any = None
    evaluator: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ---------------------------------------------------------------------------
# 私有 helpers (非 canonical 元素): 回归检测 / 版本评估
# ---------------------------------------------------------------------------


def _detect_skill_regression(
    *,
    candidate_metrics: dict[str, float],
    baseline_metrics: dict[str, float],
    metric_directions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """基于 metric_baseline_compare 的回归检测 (verdict != healthy 即回归)。"""
    if metric_baseline_compare is None:
        regressed = any(candidate_metrics.get(k, 0) < v for k, v in baseline_metrics.items())
        return {"regressed": regressed, "verdict": "degraded" if regressed else "healthy"}
    cmp_result = metric_baseline_compare(
        current_metrics=candidate_metrics,
        baseline_metrics=baseline_metrics,
        metric_directions=metric_directions,  # type: ignore[arg-type]
    )
    return {
        "regressed": cmp_result.verdict != "healthy",
        "verdict": cmp_result.verdict,
        "health_score": cmp_result.overall_health_score,
        "degraded": [d.metric_name for d in cmp_result.degraded_metrics],
    }


def _evaluate_skill_version(
    skill: dict[str, Any], cases: list[Any], evaluator: Any
) -> dict[str, float]:
    """对给定 skill 版本在 cases 上求值,返回 {metric: value}。"""
    result = evaluator(skill, cases)
    if inspect.isawaitable(result):
        import asyncio as _asyncio

        try:
            _asyncio.get_running_loop()
        except RuntimeError:
            loop = _asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(result)
            finally:
                loop.close()
        else:
            raise RuntimeError("_evaluate_skill_version: async evaluator 需要同步上下文")
    if not isinstance(result, dict):
        raise ValueError("_evaluate_skill_version: evaluator 必须返回 dict[str, float]")
    return {str(k): float(v) for k, v in result.items()}


def _score(metrics: dict[str, float], directions: dict[str, str]) -> float:
    total = 0.0
    for name, value in metrics.items():
        direction = directions.get(name, "higher_is_better")
        total += value if direction == "higher_is_better" else -value
    return total


def compute_fingerprint_for(config: Config, input_data: InputData) -> str:
    """Fingerprint over skill name + objective + case counts (不含 case 内容)。"""
    skill = input_data.skill if isinstance(input_data.skill, dict) else {}
    return compute_fingerprint(
        {
            "skill": str(skill.get("name", "")),
            "objective": config.objective,
            "n_train": len(input_data.training_cases),
            "n_valid": len(input_data.validation_cases),
        }
    )


# ---------------------------------------------------------------------------
# 主事务
# ---------------------------------------------------------------------------


def optimize_skill(config: Config, input_data: InputData, output_dir: Path) -> dict:
    """Skill 优化事务,仅返回 artifact/result,不做 promotion side effect。"""
    cost = CostTracker()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = compute_fingerprint_for(config, input_data)

    def _fail(reason: str) -> dict:
        return build_result(
            status="failed",
            error={"reason": reason},
            fingerprint=fingerprint,
            cost_usd=cost.total_usd,
            baseline_metrics={},
            candidate_metrics=[],
            selected_candidate=None,
            validation_metrics={},
            artifacts=[],
            validation_leakage=False,
        )

    # -- 输入校验 ------------------------------------------------------------
    if not isinstance(input_data.skill, dict) or not input_data.skill.get("name"):
        return _fail("skill 必须为含 name 的 dict")
    if not input_data.training_cases:
        return _fail("training_cases 不得为空")
    if not input_data.validation_cases:
        return _fail("validation_cases 不得为空")
    if input_data.candidate_generator is None or input_data.evaluator is None:
        return _fail("candidate_generator 与 evaluator 必须注入")
    train_ids = {json.dumps(c, sort_keys=True, default=str) for c in input_data.training_cases}
    valid_ids = {json.dumps(c, sort_keys=True, default=str) for c in input_data.validation_cases}
    if train_ids & valid_ids:
        return build_result(
            status="failed",
            error={"reason": "validation leakage: validation_cases 与 training_cases 重叠"},
            fingerprint=fingerprint,
            cost_usd=cost.total_usd,
            baseline_metrics={},
            candidate_metrics=[],
            selected_candidate=None,
            validation_metrics={},
            artifacts=[],
            validation_leakage=True,
        )

    source_snapshot = copy.deepcopy(input_data.skill)
    baseline_skill = copy.deepcopy(input_data.skill)

    try:
        # -- baseline (仅 training) ------------------------------------------
        baseline_metrics = _evaluate_skill_version(
            baseline_skill, list(input_data.training_cases), input_data.evaluator
        )
        cost.calls += 1

        # -- generate + evaluate candidates -----------------------------------
        candidate_metrics: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        incumbent = copy.deepcopy(baseline_skill)
        for iteration in range(1, int(config.max_iterations) + 1):
            gen_result = input_data.candidate_generator(
                copy.deepcopy(incumbent), config.objective, iteration
            )
            candidate = gen_result
            if not isinstance(candidate, dict):
                continue
            metrics = _evaluate_skill_version(
                candidate, list(input_data.training_cases), input_data.evaluator
            )
            cost.calls += 1
            regression = _detect_skill_regression(
                candidate_metrics=metrics,
                baseline_metrics=baseline_metrics,
                metric_directions=config.metric_directions or None,
            )
            candidates.append(candidate)
            candidate_metrics.append(
                {
                    "iteration": iteration,
                    "metrics": metrics,
                    "regressed": regression["regressed"],
                    "score": _score(metrics, config.metric_directions),
                }
            )
            if not regression["regressed"]:
                incumbent = copy.deepcopy(candidate)

        # -- select:非回归候选中 score 最高者 (都不合格则选 None) ----------------
        qualified = [c for c in candidate_metrics if not c["regressed"]]
        pool = qualified if qualified else []
        selected_idx: int | None = None
        if pool:
            best = max(pool, key=lambda c: c["score"])
            selected_idx = candidate_metrics.index(best)
        selected_candidate = (
            copy.deepcopy(candidates[selected_idx]) if selected_idx is not None else None
        )

        # -- held-out validation (仅 selected + baseline,不参与选择) ------------
        validation_metrics: dict[str, Any] = {}
        if selected_candidate is not None:
            selected_valid = _evaluate_skill_version(
                copy.deepcopy(selected_candidate),
                list(input_data.validation_cases),
                input_data.evaluator,
            )
            baseline_valid = _evaluate_skill_version(
                copy.deepcopy(baseline_skill),
                list(input_data.validation_cases),
                input_data.evaluator,
            )
            cost.calls += 2
            validation_metrics = {"selected": selected_valid, "baseline": baseline_valid}

        # -- 源 skill 未变断言 --------------------------------------------------
        if input_data.skill != source_snapshot:
            return _fail("source skill 被篡改 (baseline mutation)")

        # -- artifacts 落盘 (不覆盖源) ------------------------------------------
        artifacts: list[str] = []
        for fname, payload in (
            ("baseline.json", {"metrics": baseline_metrics}),
            ("candidates.json", candidate_metrics),
            ("selected.json", {"candidate": selected_candidate, "validation": validation_metrics}),
        ):
            path = output_dir / f"optimize_skill_{fname}"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            artifacts.append(str(path))

        return build_result(
            status="completed",
            fingerprint=fingerprint,
            cost_usd=cost.total_usd,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            selected_candidate=selected_candidate,
            validation_metrics=validation_metrics,
            artifacts=artifacts,
            validation_leakage=False,
        )
    except Exception as exc:
        return build_result(
            status="failed",
            error={"reason": f"{type(exc).__name__}: {exc}"},
            fingerprint=fingerprint,
            cost_usd=cost.total_usd,
            baseline_metrics={},
            candidate_metrics=[],
            selected_candidate=None,
            validation_metrics={},
            artifacts=[],
            validation_leakage=False,
        )
