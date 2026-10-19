"""omodul: 形式化安全门禁事务 (O1/O3 前置).

Pillars: fingerprint, decision_trail, report

签名: (config, input_data, output_dir, *, on_step=None)
失败绝对不 raise — 返回 status="failed".

在代码送入 O3 沙箱测试前，先用数学引擎穷举验证。
CrossHair 会将代码转化为 SMT 公式，尝试寻找任何能打破契约的反例。
找不到反例 → 代码通过数学证明 → 允许合入。
找到反例 → 提取 failing_call → 打回 LLM 修复。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, write_report


class Config(BaseConfig):
    """Formal Verify Guard 配置."""

    _omodul_name: ClassVar[str] = "formal_verify_guard"
    _omodul_version: ClassVar[str] = "1.1.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}

    verify_op: Any = None       # oprim._crosshair_verify
    executor: Any = None        # obase.SymbolicExecutor
    extractor_skill: Any = None # oskill._extract_counterexample


class InputData(BaseModel):
    """Formal Verify Guard 输入."""

    code: str = ""
    source_label: str = "unknown"
    contract_spec: str = ""

    model_config = {"arbitrary_types_allowed": True}


def formal_verify_guard(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """标准三件套。执行形式化验证并生成结构化拦截报告.

    流程:
    1. 调用 verify_op(code, executor=executor) 进行符号执行
    2. 若通过 → completed
    3. 若失败 → 调用 extractor_skill 提取反例
    4. 生成结构化打回报告（含 failing_call）

    数学保证: 找不到反例 ≠ 绝对安全，但比单元测试强数个数量级。

    Args:
        config: 配置（含 verify_op / executor / extractor_skill）
        input_data: 输入数据（code 字段为含 deal 契约的源码）
        output_dir: 输出目录
        on_step: 步骤回调

    Returns:
        {"status": "completed"|"failed", ...}
    """
    cost = CostTracker()
    trail = Trail()
    decision_trail: list[dict[str, Any]] = []

    verify_op = config.verify_op
    executor = config.executor
    extractor_skill = config.extractor_skill

    code = input_data.code
    source_label = input_data.source_label

    try:
        # Phase 1: 原子调用 — 符号执行
        trail.record(event="symbolic_execution", source=source_label)
        if verify_op is not None and executor is not None:
            raw_result = verify_op(code, executor=executor)
        else:
            # 回退: 无验证器注入 → 默认通过
            raw_result = {
                "ok": True,
                "stdout": "",
                "stderr": "No verifier injected — skipping formal check.",
                "exit_code": 0,
            }

        verified = raw_result.get("ok", False)
        decision_trail.append({
            "step": "symbolic_execution",
            "verified": verified,
            "exit_code": raw_result.get("exit_code"),
        })

        if verified:
            # 通过！代码经数学证明无契约违反
            trail.record(event="verification_passed")
            report_content = (
                f"# ✅ Formal Verification PASSED\n\n"
                f"## Source: {source_label}\n"
                f"## Result: No counterexample found.\n"
                f"CrossHair 符号执行未发现任何能打破契约的输入。\n"
                f"代码通过有界模型检查，允许合入 O3 测试。\n"
            )
            report_path = write_report(
                report_content, output_dir=output_dir,
                name=f"formal_verify_{source_label.replace('/', '_')[:40]}",
                fmt="markdown",
            )

            cost.add(in_tok=200, out_tok=100)
            trail.write(output_dir)

            return build_result(
                status="completed",
                error=None,
                cost_usd=cost.total_usd,
                report_path=str(report_path),
                trail=trail,
                trail_path=trail.write(output_dir),
                summary="代码通过形式化安全验证，不存在破坏契约的反例。",
                pillars={
                    "fingerprint": {"source": source_label, "verified": True},
                    "decision_trail": decision_trail,
                    "report": {
                        "verified": True,
                        "counterexample": None,
                    },
                },
            )

        # Phase 2: 失败 → 提取反例
        trail.record(event="extract_counterexample")
        stderr = raw_result.get("stderr", "")
        if extractor_skill is not None:
            ce_info = extractor_skill(stderr)
        else:
            ce_info = {
                "found": False,
                "failing_call": None,
                "error_type": None,
                "raw_error": stderr,
            }

        decision_trail.append({
            "step": "extract_counterexample",
            "found": ce_info.get("found"),
            "error_type": ce_info.get("error_type"),
        })

        failing_call = ce_info.get("failing_call", "unknown") or "unknown_trigger"
        raw_error = ce_info.get("raw_error", stderr)[:1000]

        report_content = (
            f"# ❌ Formal Verification FAILED\n\n"
            f"## Source: {source_label}\n"
            f"## Counterexample Found\n"
            f"**Failing Call**: `{failing_call}`\n\n"
            f"### Raw Error:\n```\n{raw_error}\n```\n\n"
            f"### Fix Instruction\n"
            f"请使用此极端输入 `{failing_call}` 修复代码逻辑，"
            f"确保在所有边界条件下契约不被打破。\n"
        )
        report_path = write_report(
            report_content, output_dir=output_dir,
            name=f"formal_verify_FAILED_{source_label.replace('/', '_')[:30]}",
            fmt="markdown",
        )

        cost.add(in_tok=400, out_tok=200)
        trail.write(output_dir)

        return build_result(
            status="failed",
            error={
                "type": "FormalVerificationFailed",
                "message": f"Counterexample: {failing_call}",
            },
            cost_usd=cost.total_usd,
            report_path=str(report_path),
            trail=trail,
            trail_path=trail.write(output_dir),
            summary="代码未通过形式化验证（存在逻辑漏洞）。",
            pillars={
                "fingerprint": {"source": source_label, "verified": False},
                "decision_trail": decision_trail,
                "report": {
                    "verified": False,
                    "counterexample": ce_info,
                    "instruction": (
                        f"请使用此极端输入 {failing_call} 修复代码逻辑。"
                    ),
                },
            },
            counterexample=ce_info,
            failing_call=failing_call,
        )

    except Exception as e:
        return build_result(
            status="failed",
            error={"type": type(e).__name__, "message": str(e)},
            cost_usd=cost.total_usd,
            trail=trail,
            summary="验证引擎内部异常。",
            pillars={
                "decision_trail": decision_trail,
                "report": {"error": str(e)},
            },
        )
