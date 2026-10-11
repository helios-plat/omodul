"""
化学方程式配平课 业务事务（omodul 层）
=======================================

标准签名: (config, input, output_dir) -> dict
支柱: decision_trail, cost, fingerprint

组合:
- oskill.balance_equation（sympy 零空间配平 + 守恒表 + 摩尔质量 + 分步解析）
- oprim.numeric_balance_check（独立整数复核，不走 sympy、不用组成矩阵）

配平结果与独立复核不一致时事务判 failed，不产出 lesson —— 宁可不给，
也不给学生一个错答案。全程不调用 LLM，cost 恒为 0。

来源：派生自 wy51ai/edulab ``skills/edu-chem-reaction``（commit
6a9c8766061ca7101d10bab6fe3af716ec47bb07，Apache-2.0，Copyright 2026 WY (@akokoi1)）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint
from oprim.chemical_equation import ReactionSpec, numeric_balance_check
from oskill.equation_balancing_skill import balance_equation


class EquationBalancingLessonConfig(BaseConfig):
    """事务配置。"""

    _omodul_name: ClassVar[str] = "equation_balancing_lesson"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost", "fingerprint"}

    write_lesson_json: bool = True


class EquationBalancingLessonInput(BaseModel):
    """事务输入。``subject_ref`` 由 Layer 4 提供（不放身份明文）。"""

    subject_ref: str = ""
    spec: ReactionSpec

    model_config = ConfigDict(extra="forbid")


def equation_balancing_lesson(
    config: EquationBalancingLessonConfig,
    input_data: EquationBalancingLessonInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """配平一个化学 / 离子方程式并产出课程数据。

    纯计算（sympy + 整数）、无 IO 并发，按 3O 本性为同步 omodul；服务层用
    ``await asyncio.to_thread(equation_balancing_lesson, ...)`` 调用。
    ``on_step`` 为同步回调 ``Callable[[dict], None]``，在每个事务步骤后调用。
    任何失败（非法化学式、无法配平、多解、复核不一致）都返回 ``status="failed"``，不抛异常。
    """
    trail = Trail()
    cost = CostTracker()
    spec = input_data.spec

    def step(event: str, **fields: object) -> None:
        trail.record(event=event, **fields)
        if on_step is not None:
            on_step({"event": event, **fields})

    fingerprint = compute_fingerprint(
        {
            "omodul": config._omodul_name,
            "version": config._omodul_version,
            "spec": spec.model_dump(mode="json"),
        }
    )
    try:
        step("spec_received", n_reactants=len(spec.reactants), n_products=len(spec.products))

        solution = balance_equation(spec)
        step("balanced", coefficients=solution.coefficients, equation_latex=solution.equation_latex)

        check = numeric_balance_check(solution.coefficients, reactants=spec.reactants, products=spec.products)
        step("cross_checked", ok=check.ok, problems=check.problems)
        if not check.ok:
            raise ValueError(f"independent conservation check failed: {'; '.join(check.problems)}")

        lesson_path = None
        if config.write_lesson_json:
            output_dir.mkdir(parents=True, exist_ok=True)
            lesson_path = output_dir / f"equation_balancing_lesson_{fingerprint}.json"
            lesson_path.write_text(json.dumps(solution.lesson_data, ensure_ascii=False), encoding="utf-8")
        trail_path = trail.write(output_dir) if output_dir.exists() else None

        return build_result(
            status="completed",
            error=None,
            fingerprint=fingerprint,
            trail=trail,
            trail_path=trail_path,
            report_path=lesson_path,
            cost_usd=cost.total_usd,
            solution=solution.model_dump(),
        )
    except Exception as exc:  # noqa: BLE001 - omodul 事务边界统一收口
        trail.record(event="error", error_type=type(exc).__name__, message=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fingerprint,
            trail=trail,
            trail_path=None,
            cost_usd=cost.total_usd,
        )
