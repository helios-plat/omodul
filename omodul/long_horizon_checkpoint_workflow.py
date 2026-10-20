"""omodul.long_horizon_checkpoint_workflow — 带 Checkpoint 快照与断点恢复能力的长程事务.

Pillars: decision_trail, cost

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
    """Long Horizon Checkpoint Workflow 配置."""

    _omodul_name: ClassVar[str] = "long_horizon_checkpoint_workflow"
    _omodul_version: ClassVar[str] = "1.1.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}

    checkpoint_store: Any = None
    max_steps: int = 100


class InputData(BaseModel):
    """Long Horizon Checkpoint Workflow 输入."""

    session_id: str = "default_session"
    task: str = ""
    step_payload: dict[str, Any] = {}

    model_config = {"arbitrary_types_allowed": True}


def long_horizon_checkpoint_workflow(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """长程任务带 Checkpoint 快照与断点恢复能力的业务事务.

    流程:
    1. 从 checkpoint_store 尝试装载现有状态（水合恢复）
    2. 执行当前步骤
    3. 将新状态持久化落盘

    Args:
        config: 配置（含注入的 checkpoint_store）
        input_data: 输入数据
        output_dir: 输出目录
        on_step: 步骤回调（可选）

    Returns:
        {"status": "completed"|"failed", ...}
    """
    cost = CostTracker()
    trail = Trail()
    decision_trail: list[dict[str, Any]] = []
    store = config.checkpoint_store

    session_id = input_data.session_id

    try:
        # 1. 水合：装载现有 Checkpoint
        trail.record(event="load_checkpoint", session_id=session_id)
        existing = {}
        if store is not None and hasattr(store, "load_checkpoint"):
            try:
                existing = store.load_checkpoint(session_id)
            except Exception:
                existing = {}

        step_index = existing.get("step_index", 0) + 1
        history = existing.get("history", [])
        history.append({
            "step": step_index,
            "task": input_data.task,
            "payload": input_data.step_payload,
        })

        decision_trail.append({
            "step": "execute_long_task",
            "step_index": step_index,
            "session_id": session_id,
        })

        # 2. 构建新状态并持久化
        state_to_save = {
            "session_id": session_id,
            "step_index": step_index,
            "task": input_data.task,
            "history": history[-50:],  # 保留最近 50 步
            "status": "in_progress",
        }

        if store is not None and hasattr(store, "save_checkpoint"):
            try:
                store.save_checkpoint(session_id, state_to_save)
                decision_trail.append({"step": "checkpoint_saved", "step_index": step_index})
            except Exception as e:
                decision_trail.append({
                    "step": "checkpoint_save_failed",
                    "error": str(e),
                })

        cost.add(in_tok=100, out_tok=50)

        # 3. 生成报告
        report_content = (
            f"# Long Horizon Checkpoint Report\n\n"
            f"## Session: {session_id}\n"
            f"## Step: {step_index}\n"
            f"## Status: in_progress\n\n"
            f"## History Length: {len(history)}\n"
        )
        report_path = write_report(
            report_content,
            output_dir=output_dir,
            name=f"checkpoint_{session_id}",
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
            summary=f"Checkpoint step {step_index} recorded for session {session_id}",
            pillars={
                "decision_trail": decision_trail,
                "cost": {
                    "checkpoint_saves": 1,
                    "step_index": step_index,
                    "history_length": len(history),
                },
            },
            session_id=session_id,
            step_index=step_index,
        )

    except Exception as e:
        return build_result(
            status="failed",
            error={"type": type(e).__name__, "message": str(e)},
            cost_usd=cost.total_usd,
            trail=trail,
            summary=f"Long-Horizon Checkpoint Workflow failed: {str(e)}",
            pillars={"decision_trail": decision_trail},
        )
