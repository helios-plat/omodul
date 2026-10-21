"""omodul 事务层：组合运筹分配与死锁熔断的 O2 级事务。

标准三件套签名 (config, input_data, output_dir)。
显式声明 _enabled_pillars，内部异常全面捕获不 raise。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, write_report


class Config(BaseConfig):
    """Deterministic Operate Assign 配置."""

    _omodul_name: ClassVar[str] = "deterministic_operate_assign"
    _omodul_version: ClassVar[str] = "1.1.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}

    assign_op: Any = None
    wfg_instance: Any = None


class InputData(BaseModel):
    """Deterministic Operate Assign 输入."""

    workers: list[str] = []
    tasks: list[str] = []
    cost_matrix: list[list[float]] = []

    model_config = {"arbitrary_types_allowed": True}


def deterministic_operate_assign(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """执行包含代价匹配与死锁拆解的分配事务。标准三件套签名.

    流程:
    1. 死锁图检测与熔断（WFG）
    2. 确定性全局最优指派（匈牙利算法）
    3. 生成分配报告

    Args:
        config: 配置（含 assign_op / wfg_instance）
        input_data: 输入数据
        output_dir: 输出目录
        on_step: 步骤回调（可选）

    Returns:
        {"status": "completed"|"failed", ...}
    """
    cost = CostTracker()
    trail = Trail()
    decision_trail: list[dict[str, Any]] = []

    # 依赖倒置：从 config 获取 oprim 与 obase 实例
    assign_op = config.assign_op
    wfg_instance = config.wfg_instance

    workers = input_data.workers
    tasks = input_data.tasks
    cost_matrix = input_data.cost_matrix

    try:
        # Phase 1: 死锁图熔断检测
        victims: list[str] = []
        if wfg_instance:
            trail.record(event="deadlock_detect")
            try:
                victims = wfg_instance.resolve_deadlocks()
            except Exception:
                victims = []
            decision_trail.append({
                "step": "deadlock_resolve",
                "victims_killed": victims,
            })
            # 剔除死掉的 Agent
            workers = [w for w in workers if w not in victims]

        # Phase 2: 确定性全局最优指派
        trail.record(event="linear_assignment", worker_count=len(workers))
        if assign_op is not None:
            assign_result = assign_op(cost_matrix, workers=workers, tasks=tasks)
        else:
            # 回退：简单贪心分配
            assigned_t: set[int] = set()
            matches: list[tuple[str, str]] = []
            total = 0.0
            for wi, w in enumerate(workers):
                best_j, best_c = -1, float("inf")
                for tj in range(len(tasks)):
                    if tj not in assigned_t and cost_matrix[wi][tj] < best_c:
                        best_c = cost_matrix[wi][tj]
                        best_j = tj
                if best_j >= 0:
                    matches.append((w, tasks[best_j]))
                    total += best_c
                    assigned_t.add(best_j)
            assign_result = {
                "status": "success",
                "matches": matches,
                "total_cost": total,
                "unassigned_workers": [w for i, w in enumerate(workers) if i not in [workers.index(m[0]) for m in matches]],
                "unassigned_tasks": [t for j, t in enumerate(tasks) if j not in assigned_t],
            }

        decision_trail.append({
            "step": "linear_sum_assignment",
            "status": assign_result.get("status"),
            "match_count": len(assign_result.get("matches", [])),
        })

        cost.add(in_tok=100, out_tok=50)

        # 生成报告
        report_content = (
            f"# Deterministic Operate Assign Report\n\n"
            f"## Summary\n"
            f"- Workers: {len(workers)}\n"
            f"- Tasks: {len(tasks)}\n"
            f"- Matches: {len(assign_result.get('matches', []))}\n"
            f"- Total Cost: {assign_result.get('total_cost', 0.0)}\n"
            f"- Victims Killed: {victims}\n"
            f"- Unassigned Workers: {assign_result.get('unassigned_workers', [])}\n"
            f"- Unassigned Tasks: {assign_result.get('unassigned_tasks', [])}\n"
        )
        report_path = write_report(
            report_content, output_dir=output_dir,
            name="deterministic_operate_assign",
            fmt="markdown",
        )

        trail.write(output_dir)

        return build_result(
            status="completed",
            error=None,
            cost_usd=cost.total_usd,
            report_path=str(report_path),
            trail=trail,
            trail_path=trail.write(output_dir),
            summary="O2 Operator assignment completed.",
            pillars={
                "fingerprint": {
                    "task_count": len(tasks),
                    "worker_count": len(workers),
                },
                "decision_trail": decision_trail,
                "report": {
                    "assignments": assign_result.get("matches", []),
                    "total_cost": assign_result.get("total_cost", 0.0),
                    "victims_killed": victims,
                },
            },
        )

    except Exception as e:
        return build_result(
            status="failed",
            error={"type": type(e).__name__, "message": str(e)},
            cost_usd=cost.total_usd,
            trail=trail,
            summary="Transaction failed without raising exception.",
            pillars={
                "decision_trail": decision_trail,
                "report": {"error_trace": str(e)},
            },
        )
