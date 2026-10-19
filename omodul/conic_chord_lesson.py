"""
圆锥曲线过定点弦交互解题课 业务事务（omodul 层）
===================================================

标准签名: (config, input, output_dir) -> dict
支柱: decision_trail, cost, fingerprint

组合:
- oskill.solve_conic_chord（sympy 精确求解 + 分步解析 + 交互画板数据）
- oprim.numeric_range_check（独立浮点复核：按倾斜角密集采样直接求交点，不走 sympy / 韦达）

事件: spec_received → solved → cross_checked。精确取值范围与独立浮点复核不一致时
事务判 failed，不产出 lesson —— 宁可不给，也不给学生一个错答案。全程不调用 LLM，
cost 恒为 0。

复核规则（每段区间的每个端点）：
- 有限闭端点：采样（含局部极值黄金分割细化）须在 ``cross_check_tolerance`` 相对误差内取到；
- 有限开端点：采样不得越过端点，且须在 ``approach_tolerance`` 内逼近（边界二分细化）；
- 无界端点：采样量级须超过 ``unbounded_factor × 尺度``；
- 定值：全部采样落在定值的 ``cross_check_tolerance`` 内；
- 区间段数须一致（仅在单点"洞"处相接的段视为同一段比较）。

来源：题型派生自 wy51ai/edulab ``skills/edu-analytic-geometry``（commit
6a9c8766061ca7101d10bab6fe3af716ec47bb07，Apache-2.0，Copyright 2026 WY (@akokoi1)）。

持久化 / HTML 渲染: Layer 4（替换 board.html 的 ``__LESSON_DATA__`` 占位符）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from omodul._base import (
    BaseConfig,
    CostTracker,
    Trail,
    build_result,
    compute_fingerprint,
)
from pydantic import BaseModel, ConfigDict

from oprim.conic_analysis import AnalyticGeometrySpec, numeric_range_check
from oskill.conic_chord_skill import ConicChordSolution, solve_conic_chord


class ConicChordLessonConfig(BaseConfig):
    """事务配置。"""

    _omodul_name: ClassVar[str] = "conic_chord_lesson"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost", "fingerprint"}

    samples: int = 36000
    cross_check_tolerance: float = 1e-6
    approach_tolerance: float = 1e-4
    unbounded_factor: float = 1e4
    write_lesson_json: bool = True


class ConicChordLessonInput(BaseModel):
    """事务输入。``subject_ref`` 由 Layer 4 提供（不放身份明文）。"""

    subject_ref: str = ""
    spec: AnalyticGeometrySpec

    model_config = ConfigDict(extra="forbid")


def _compare(
    solution: ConicChordSolution,
    numeric: list[tuple[float, float]],
    *,
    tol: float,
    approach: float,
    unbounded_factor: float,
) -> tuple[bool, str]:
    """精确区间（并）与数值区间（并）逐端点比对，返回 (是否一致, 说明)。"""
    pieces = solution.answer_pieces
    finite = [abs(v) for pc in pieces for v in (pc.lo, pc.hi) if v is not None]
    scale = max([1.0, *finite])
    if solution.answer_kind == "constant":
        c = solution.constant_value
        assert c is not None
        lo, hi = numeric[0][0], numeric[-1][1]
        ok = abs(lo - c) <= tol * max(1.0, abs(c)) and abs(hi - c) <= tol * max(1.0, abs(c))
        return ok, f"constant={c} sampled=[{lo}, {hi}]"

    # 仅在单点洞（两开端点相接）处分开的精确段，数值采样无法分辨，比较时视为一段
    exact: list[list] = []
    for pc in pieces:
        if exact and exact[-1][1] is not None and pc.lo is not None and abs(exact[-1][1] - pc.lo) <= tol * scale:
            exact[-1][1], exact[-1][3] = pc.hi, pc.hi_closed
        else:
            exact.append([pc.lo, pc.hi, pc.lo_closed, pc.hi_closed])
    if len(exact) != len(numeric):
        return False, f"piece count differs: exact={len(exact)} numeric={len(numeric)}"

    big = unbounded_factor * scale
    for (elo, ehi, lc, hc), (nlo, nhi) in zip(exact, numeric):
        for e, n, closed, side in ((elo, nlo, lc, -1), (ehi, nhi, hc, 1)):
            if e is None:
                if side * n < big:
                    return False, f"expected unbounded end, sampled {n}"
                continue
            room = tol * max(1.0, abs(e))
            if side * (n - e) > room:
                return False, f"sample {n} beyond exact endpoint {e}"
            gap = abs(n - e)
            if closed and gap > room:
                return False, f"closed endpoint {e} not attained (sampled {n})"
            if not closed and gap > approach * max(1.0, abs(e)):
                return False, f"open endpoint {e} not approached (sampled {n})"
    return True, "ok"


def conic_chord_lesson(
    config: ConicChordLessonConfig,
    input_data: ConicChordLessonInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict:
    """解一道圆锥曲线过定点弦题并产出交互课数据。

    纯计算（sympy + math）、无 IO 并发，按 3O 本性为同步 omodul；服务层用
    ``await asyncio.to_thread(conic_chord_lesson, ...)`` 调用。
    ``on_step`` 为同步回调 ``Callable[[dict], None]``，在每个事务步骤后调用。
    任何异常都收口为 ``status="failed"``，从不向外抛出。
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
            "samples": config.samples,
            "spec": spec.model_dump(mode="json"),
        }
    )
    try:
        step("spec_received", conic_kind=spec.conic.kind, target=spec.query.target)

        solution = solve_conic_chord(spec)
        step("solved", answer_latex=solution.answer_latex, answer_kind=solution.answer_kind,
             n_steps=len(solution.steps))

        check = numeric_range_check(
            solution.conic_kind,
            a=solution.conic_floats.get("a", 0.0),
            b=solution.conic_floats.get("b", 0.0),
            p=solution.conic_floats.get("p", 0.0),
            through=(solution.through[0], solution.through[1]),
            target=solution.target,
            vertex=None if solution.vertex is None else (solution.vertex[0], solution.vertex[1]),
            samples=config.samples,
        )
        ok, detail = _compare(
            solution, check.pieces,
            tol=config.cross_check_tolerance,
            approach=config.approach_tolerance,
            unbounded_factor=config.unbounded_factor,
        )
        step("cross_checked", numeric_pieces=[list(pc) for pc in check.pieces], n_valid=check.n_valid,
             n_samples=check.n_samples, ok=ok, detail=detail)
        if not ok:
            raise ValueError(f"cross-check mismatch: exact={solution.answer_latex} numeric={check.pieces} ({detail})")

        lesson_path = None
        if config.write_lesson_json:
            output_dir.mkdir(parents=True, exist_ok=True)
            lesson_path = output_dir / f"conic_chord_lesson_{fingerprint}.json"
            lesson_path.write_text(json.dumps(solution.board_data, ensure_ascii=False))
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
