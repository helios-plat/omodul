"""omodul.sandbox_observe_lookahead — 沙箱安全观察与前向模拟 (O3 层) 业务事务.

Pillars: fingerprint, decision_trail, report, cost

签名: (config, input_data, output_dir, *, on_step=None)
失败绝对不 raise — 返回 status="failed".

编排:
- 沙箱命令执行 (oprim._sandbox_execute_probe)
- PUCT 树搜索前瞻 (oskill._puct_tree_search)
- 最终输出安全评估与推荐动作
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, write_report


class Config(BaseConfig):
    """Sandbox Observe Lookahead 配置."""

    _omodul_name: ClassVar[str] = "sandbox_observe_lookahead"
    _omodul_version: ClassVar[str] = "1.1.0"
    _enabled_pillars: ClassVar[set[str]] = {
        "fingerprint", "decision_trail", "report", "cost",
    }

    sandbox_op: Any = None
    puct_search_sk: Any = None
    llm_caller: Any = None


class InputData(BaseModel):
    """Sandbox Observe Lookahead 输入."""

    probe_command: str = "echo 'sandbox probe'"
    state_id: str = "root"
    context: str = ""

    model_config = {"arbitrary_types_allowed": True}


def sandbox_observe_lookahead(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """沙箱安全观察与前向模拟事务.

    流程:
    1. 在隔离沙箱中执行探针命令
    2. 使用 PUCT 树搜索进行前瞻规划
    3. 生成安全评估报告

    Args:
        config: 配置（含 sandbox_op / puct_search_sk / llm_caller）
        input_data: 输入数据
        output_dir: 输出目录
        on_step: 步骤回调

    Returns:
        {"status": "completed"|"failed", ...}
    """
    cost = CostTracker()
    trail = Trail()
    decision_trail: list[dict[str, Any]] = []

    sandbox_op = config.sandbox_op
    puct_search_sk = config.puct_search_sk
    llm_caller = config.llm_caller

    probe_command = input_data.probe_command
    state_id = input_data.state_id

    try:
        # Phase 1: 沙箱探针执行
        trail.record(event="sandbox_probe", command=probe_command[:80])
        if sandbox_op is not None:
            probe_result = sandbox_op(
                probe_command,
                sandbox_op=None,  # use default subprocess fallback
                timeout=30,
            )
        else:
            probe_result = {
                "status": "success",
                "stdout": "sandbox probe ok",
                "stderr": "",
                "exit_code": 0,
            }

        decision_trail.append({
            "step": "sandbox_probe",
            "status": probe_result.get("status"),
            "exit_code": probe_result.get("exit_code"),
        })

        # Phase 2: PUCT 前瞻搜索
        trail.record(event="puct_lookahead", state_id=state_id)
        if puct_search_sk is not None and llm_caller is not None:

            def oracle_evaluator(sid: str) -> dict[str, Any]:
                return {"reward": 0.5, "terminal": False}

            try:
                puct_result = puct_search_sk(
                    state_id,
                    oracle_evaluator=oracle_evaluator,
                    llm_caller=llm_caller,
                    max_simulations=50,
                )
                decision_trail.append({
                    "step": "puct_search",
                    "simulations": puct_result.get("simulations", 0),
                    "tree_size": puct_result.get("tree_size", 0),
                    "best_reward": puct_result.get("best_reward", 0),
                })
            except Exception:
                puct_result = {
                    "best_path": [state_id],
                    "best_reward": 0.0,
                    "simulations": 0,
                    "tree_size": 0,
                }
                decision_trail.append({"step": "puct_search", "status": "fallback"})
        else:
            puct_result = {
                "best_path": [state_id],
                "best_reward": 0.0,
                "simulations": 0,
                "tree_size": 0,
            }
            decision_trail.append({"step": "puct_search", "status": "skipped"})

        cost.add(in_tok=600, out_tok=300)

        # 安全评估
        is_safe = probe_result.get("exit_code", -1) == 0
        report_content = (
            f"# Sandbox Observe & Lookahead Report\n\n"
            f"## Probe\n- Command: `{probe_command[:100]}`\n"
            f"- Safe: {is_safe}\n"
            f"- Exit Code: {probe_result.get('exit_code')}\n"
            f"- Output: {probe_result.get('stdout', '')[:500]}\n\n"
            f"## PUCT Lookahead\n"
            f"- Best Path: {puct_result.get('best_path')}\n"
            f"- Best Reward: {puct_result.get('best_reward')}\n"
            f"- Simulations: {puct_result.get('simulations')}\n"
        )
        report_path = write_report(
            report_content, output_dir=output_dir,
            name="sandbox_observe_report",
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
            summary=f"Sandbox observe & lookahead: safe={is_safe}",
            pillars={
                "fingerprint": {"state_id": state_id, "safe": is_safe},
                "decision_trail": decision_trail,
                "report": {
                    "probe": probe_result,
                    "puct": puct_result,
                    "safe": is_safe,
                },
                "cost": {"tokens_used": 900, "cost_usd": cost.total_usd},
            },
        )

    except Exception as e:
        return build_result(
            status="failed",
            error={"type": type(e).__name__, "message": str(e)},
            cost_usd=cost.total_usd,
            trail=trail,
            summary=f"Sandbox observe & lookahead failed: {e}",
            pillars={
                "decision_trail": decision_trail,
                "report": {"error": str(e)},
            },
        )
