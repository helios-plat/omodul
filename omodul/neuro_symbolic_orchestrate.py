"""omodul.neuro_symbolic_orchestrate — 神经符号融合编排 (O1 层) 业务事务.

Pillars: fingerprint, decision_trail, report, cost

签名: (config, input_data, output_dir, *, on_step=None)
失败绝对不 raise — 返回 status="failed".

编排:
- Z3 符号约束求解 (oprim._z3_check_feasible)
- MUS 最小矛盾核收缩 (oskill._mus_shrink_algorithm)
- 最终输出可解释的冲突诊断与修复建议
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, write_report


class Config(BaseConfig):
    """Neuro-Symbolic Orchestrate 配置."""

    _omodul_name: ClassVar[str] = "neuro_symbolic_orchestrate"
    _omodul_version: ClassVar[str] = "1.1.0"
    _enabled_pillars: ClassVar[set[str]] = {
        "fingerprint", "decision_trail", "report", "cost",
    }

    z3_check_op: Any = None
    mus_shrink_sk: Any = None
    llm_caller: Any = None


class InputData(BaseModel):
    """Neuro-Symbolic Orchestrate 输入."""

    smt2_spec: str = ""
    constraints: list[str] = []
    context: str = ""

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# 轻量 IR 结构校验器（Z3 不可用时仍能检测格式错误）
# ---------------------------------------------------------------------------

_VALID_OPS: set[str] = {
    ">=", "<=", ">", "<", "==", "!=",
    "+", "-", "*", "/", "and", "or", "not",
    "min", "max", "abs",
}


def _validate_ir_constraints(constraints: list[str]) -> list[str]:
    """校验 IR 约束的结构合法性."""
    import json

    issues: list[str] = []
    for i, c_str in enumerate(constraints):
        try:
            c = json.loads(c_str) if isinstance(c_str, str) else c_str
        except (json.JSONDecodeError, TypeError):
            issues.append(f"c{i}: 无法解析为 JSON")
            continue

        if not isinstance(c, dict):
            issues.append(f"c{i}: 不是 JSON 对象")
            continue

        expr = c.get("expr", {})
        if not isinstance(expr, dict):
            issues.append(f"c{i}: 缺少 expr 字段")
            continue

        op = expr.get("op", "")
        if not op:
            issues.append(f"c{i}: expr 缺少 op 字段")
        elif op not in _VALID_OPS:
            issues.append(f"c{i}: 未知操作符 '{op}' (已知: {sorted(_VALID_OPS)})")

        args = expr.get("args")
        if args is not None and not isinstance(args, list):
            issues.append(f"c{i}: expr.args 必须是数组")

    return issues


def neuro_symbolic_orchestrate(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """神经符号融合编排事务.

    流程:
    1. Z3 符号约束可满足性检查
    2. 若 UNSAT → MUS 收缩定位矛盾核
    3. LLM 将矛盾核翻译为人类可读的冲突诊断

    Args:
        config: 配置（含 z3_check_op / mus_shrink_sk / llm_caller）
        input_data: 输入数据
        output_dir: 输出目录
        on_step: 步骤回调

    Returns:
        {"status": "completed"|"failed", ...}
    """
    cost = CostTracker()
    trail = Trail()
    decision_trail: list[dict[str, Any]] = []

    z3_check_op = config.z3_check_op
    mus_shrink_sk = config.mus_shrink_sk
    llm_caller = config.llm_caller

    smt2_spec = input_data.smt2_spec
    constraints = input_data.constraints

    try:
        # Phase 1: Z3 可满足性检查
        trail.record(event="z3_feasibility_check")
        if z3_check_op is not None and smt2_spec:
            check_res = z3_check_op(smt2_spec)
        elif z3_check_op is not None and constraints:
            check_res = {"verdict": "unknown", "error": None}
        else:
            check_res = {"verdict": "sat", "error": None}

        verdict = check_res.get("verdict", "unknown")
        decision_trail.append({"step": "z3_check", "verdict": verdict})

        # Phase 1b: 轻量 IR 结构校验（当 Z3 不可用时仍能检测格式错误）
        ir_issues: list[str] = []
        if z3_check_op is None and constraints:
            ir_issues = _validate_ir_constraints(constraints)
        elif verdict == "sat" and constraints:
            # Z3 返回 sat，仍需检查 IR 是否含有非法 op
            ir_issues = _validate_ir_constraints(constraints)
            if ir_issues:
                verdict = "unsat"

        if ir_issues:
            decision_trail.append({"step": "ir_validation", "issues": ir_issues})

        # Phase 2: 若 UNSAT → MUS 收缩
        mus_result: dict[str, Any] = {}
        if verdict == "unsat" and mus_shrink_sk is not None and constraints:
            trail.record(event="mus_shrink", constraint_count=len(constraints))

            def oracle(subset):
                if z3_check_op is not None:
                    import json
                    joint = "\n".join(subset)
                    res = z3_check_op(joint)
                    return res.get("verdict", "unknown")
                return "unknown"

            try:
                mus_result = mus_shrink_sk(
                    constraints,
                    oracle_checker=oracle,
                    max_checks=100,
                )
            except Exception:
                mus_result = {"mus": constraints, "verified": False, "checks": 0, "dropped": []}

            decision_trail.append({
                "step": "mus_shrunk",
                "original_count": len(constraints),
                "mus_count": len(mus_result.get("mus", [])),
                "verified": mus_result.get("verified"),
            })

        # Phase 3: LLM 生成诊断
        trail.record(event="llm_diagnose")
        if llm_caller is not None:
            mus_core = mus_result.get("mus", [])
            prompt = (
                f"SMT Solver Verdict: {verdict}\n"
                f"IR Issues: {ir_issues}\n"
                f"Context: {input_data.context}\n"
                f"Conflicting Constraints (MUS): {mus_core[:10]}\n\n"
                f"Provide a concise diagnosis of the conflict and suggest a fix. "
                f"Reply in 2-4 sentences."
            )
            try:
                diagnosis = llm_caller(prompt, temperature=0.0).strip()
            except Exception:
                diagnosis = f"Conflict detected in {len(mus_core)} constraints. Manual review needed."
        else:
            if ir_issues:
                diagnosis = "IR 结构问题: " + "; ".join(ir_issues[:5])
            elif mus_result.get("mus"):
                diagnosis = f"MUS 核心: {mus_result.get('mus', [])}"
            else:
                diagnosis = f"Verdict: {verdict}. No diagnostic available."

        # 确定最终状态：有 IR 问题时标记为 needs_revision
        if ir_issues:
            final_status = "needs_revision"
        elif verdict == "sat":
            final_status = "completed"
        elif verdict == "unsat":
            final_status = "needs_revision"
        else:
            final_status = "completed"

        cost.add(in_tok=800, out_tok=400)

        report_content = (
            f"# Neuro-Symbolic Orchestration Report\n\n"
            f"## Verdict: {verdict}\n"
            f"## MUS Core: {mus_result.get('mus', [])}\n"
            f"## Diagnosis: {diagnosis}\n"
        )
        report_path = write_report(
            report_content, output_dir=output_dir,
            name="neuro_symbolic_report",
            fmt="markdown",
        )

        trail.write(output_dir)

        return build_result(
            status=final_status,
            error=None,
            cost_usd=cost.total_usd,
            report_path=str(report_path),
            trail=trail,
            trail_path=trail.write(output_dir),
            summary=f"Neuro-symbolic orchestration: verdict={verdict}",
            pillars={
                "fingerprint": {"verdict": verdict, "constraint_count": len(constraints)},
                "decision_trail": decision_trail,
                "report": {
                    "verdict": verdict,
                    "mus": mus_result,
                    "diagnosis": diagnosis,
                    "ir_issues": ir_issues,
                },
                "cost": {"tokens_used": 1200, "cost_usd": cost.total_usd},
            },
        )

    except Exception as e:
        return build_result(
            status="failed",
            error={"type": type(e).__name__, "message": str(e)},
            cost_usd=cost.total_usd,
            trail=trail,
            summary=f"Neuro-symbolic orchestration failed: {e}",
            pillars={
                "decision_trail": decision_trail,
                "report": {"error": str(e)},
            },
        )
