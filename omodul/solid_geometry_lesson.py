"""
立体几何交互解题课 业务事务（omodul 层）
===========================================

标准签名: (config, input, output_dir) -> dict
支柱: decision_trail, cost, fingerprint

组合:
- oskill.solve_solid_geometry（sympy 精确求解 + 分步解析 + 3D 模型）
- oprim.numeric_query_value（独立浮点复核，不走 sympy）

精确解与独立浮点复核不一致时事务判 failed，不产出 lesson —— 宁可不给，
也不给学生一个错答案。全程不调用 LLM，cost 恒为 0。

持久化 / HTML 渲染: services/solid_geometry_service.py (Layer 4)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint
from oprim.solid_geometry import QUERY_ARITY, SolidGeometrySpec, numeric_query_value
from oskill.solid_geometry_skill import solve_solid_geometry


class SolidGeometryLessonConfig(BaseConfig):
    """事务配置。"""

    _omodul_name: ClassVar[str] = "solid_geometry_lesson"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost", "fingerprint"}

    scale: float = 1.5
    cross_check_tolerance: float = 1e-9
    write_lesson_json: bool = True


class SolidGeometryLessonInput(BaseModel):
    """事务输入。``subject_ref`` 由 Layer 4 提供（不放身份明文）。"""

    subject_ref: str = ""
    spec: SolidGeometrySpec

    model_config = ConfigDict(extra="forbid")


async def solid_geometry_lesson(
    config: SolidGeometryLessonConfig,
    input_data: SolidGeometryLessonInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """解一道立体几何题并产出交互课数据。"""
    trail = Trail()
    cost = CostTracker()
    spec = input_data.spec
    fingerprint = compute_fingerprint(
        {
            "omodul": config._omodul_name,
            "version": config._omodul_version,
            "scale": config.scale,
            "spec": spec.model_dump(mode="json"),
        }
    )
    try:
        trail.record(event="spec_received", body=spec.body, query_type=spec.query.type, n_givens=len(spec.givens))

        solution = solve_solid_geometry(spec, scale=config.scale)
        trail.record(event="solved", answer_latex=solution.answer_latex, n_steps=len(solution.steps))

        coords = [
            solution.math_points[name]
            for key in QUERY_ARITY[spec.query.type]
            for name in spec.query.operand(key)
        ]
        check = numeric_query_value(spec.query.type, coords)
        delta = abs(check - solution.answer_value)
        ok = delta <= config.cross_check_tolerance * max(1.0, abs(check))
        trail.record(event="cross_checked", numeric=check, exact=solution.answer_value, delta=delta, ok=ok)
        if not ok:
            raise ValueError(f"cross-check mismatch: exact={solution.answer_value} numeric={check}")

        lesson_path = None
        if config.write_lesson_json:
            output_dir.mkdir(parents=True, exist_ok=True)
            lesson_path = output_dir / f"solid_geometry_lesson_{fingerprint}.json"
            lesson_path.write_text(json.dumps(solution.lesson_data, ensure_ascii=False))
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
