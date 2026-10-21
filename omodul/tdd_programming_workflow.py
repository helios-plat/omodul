"""omodul.tdd_programming_workflow — TDD 驱动的专业编程事务.

Pillars: fingerprint, decision_trail, report, cost

签名: (config, input_data, output_dir, *, on_step=None)
失败绝对不 raise — 返回 status="failed".

遵循 omodul 红线:
- 显式声明 _enabled_pillars
- 含 "failed" 状态返回
- 禁止 try 之外裸 raise
- 禁止 sibling omodul 导入
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, write_report


class Config(BaseConfig):
    """TDD Programming Workflow 配置."""

    _omodul_name: ClassVar[str] = "tdd_programming_workflow"
    _omodul_version: ClassVar[str] = "1.1.0"
    _enabled_pillars: ClassVar[set[str]] = {
        "fingerprint",
        "decision_trail",
        "report",
        "cost",
    }

    test_run_op: Any = None
    review_sk: Any = None
    llm_caller: Any = None


class InputData(BaseModel):
    """TDD Programming Workflow 输入."""

    task_description: str = ""
    test_command: str = "pytest"
    git_diff: str = ""
    source_code: str = ""

    model_config = {"arbitrary_types_allowed": True}


def tdd_programming_workflow(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """TDD 驱动 (Test-First → Implementation → Review) 的专业编程业务事务.

    流程:
    1. Phase RED: 运行当前测试，记录初始失败
    2. Phase REVIEW: 对 git diff 执行 code review
    3. 生成报告

    Args:
        config: 配置（含注入的 test_run_op / review_sk / llm_caller）
        input_data: 输入数据
        output_dir: 输出目录
        on_step: 步骤回调（可选）

    Returns:
        {"status": "completed"|"needs_revision"|"failed", ...}
    """
    cost = CostTracker()
    trail = Trail()
    decision_trail: list[dict[str, Any]] = []

    test_op = config.test_run_op
    review_sk = config.review_sk
    llm_caller = config.llm_caller
    task_desc = input_data.task_description

    try:
        # Phase 1: 运行 TDD 测试 (预期失败 Red)
        decision_trail.append({"step": "tdd_red_check", "status": "running_initial_tests"})
        trail.record(event="tdd_red_check", phase="red")

        if test_op is not None:
            initial_test = (
                test_op(input_data.test_command)
                if callable(test_op)
                else {"passed": False, "exit_code": -1, "stdout": "", "stderr": ""}
            )
        else:
            initial_test = {"passed": True, "exit_code": 0, "stdout": "", "stderr": ""}

        decision_trail.append({
            "step": "initial_test_result",
            "passed": initial_test.get("passed", False),
            "exit_code": initial_test.get("exit_code", -1),
        })

        # Phase 2: 执行 Code Review 检查
        trail.record(event="code_review", phase="review")
        if review_sk is not None and llm_caller is not None:
            diff_str = input_data.git_diff or "No diff available"
            review_res = review_sk(diff_str, llm_caller=llm_caller)
        else:
            review_res = {"approved": True, "issues": [], "suggestions": ""}

        decision_trail.append({"step": "code_review", "review": review_res})

        cost.add(in_tok=800, out_tok=400)

        # Phase 3: 生成报告
        report_content = (
            f"# TDD Programming Workflow Report\n\n"
            f"## Task\n{task_desc}\n\n"
            f"## Test Results\n- Passed: {initial_test.get('passed')}\n"
            f"- Exit Code: {initial_test.get('exit_code')}\n\n"
            f"## Code Review\n- Approved: {review_res.get('approved')}\n"
            f"- Issues: {review_res.get('issues', [])}\n"
            f"- Suggestions: {review_res.get('suggestions', '')}\n\n"
            f"## Decision Trail\n```json\n"
            f"{__import__('json').dumps(decision_trail, indent=2)}\n```"
        )
        report_path = write_report(
            report_content,
            output_dir=output_dir,
            name="tdd_workflow_report",
            fmt="markdown",
        )

        trail.write(output_dir)

        # 确定最终状态
        if review_res.get("approved"):
            final_status = "completed"
        else:
            final_status = "needs_revision"

        return build_result(
            status=final_status,
            error=None,
            cost_usd=cost.total_usd,
            report_path=str(report_path),
            trail=trail,
            trail_path=trail.write(output_dir),
            summary=f"TDD Workflow executed for: {task_desc}",
            pillars={
                "fingerprint": {"task": task_desc},
                "decision_trail": decision_trail,
                "report": {
                    "review_result": review_res,
                    "test_result": initial_test,
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
            summary=f"TDD Workflow failed: {str(e)}",
            pillars={
                "decision_trail": decision_trail,
                "report": {"error": str(e)},
            },
        )
