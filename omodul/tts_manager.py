"""3O 范式内化 — ReasoningBank 测试时计算缩放控制器 (omodul).

评估任务复杂度 → HARD_TASK 提升 MCTS 宽度 (N=3→6) →
检索历史反思经验作为硬约束注入。只产出决策, 不执行演化
(同层防火墙: 编排由 oservi 完成)。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from obase import OmodulBaseConfig
from obase.memory_bank import MemoryBank
from pydantic import BaseModel


class TTSConfig(OmodulBaseConfig):
    _omodul_name = "tts_manager"
    _omodul_version = "1.0.0"
    _enabled_pillars = {"decision_trail", "fingerprint"}
    _fingerprint_fields = {"anon_task_ref", "task_description"}

    normal_branch_count: int = 3
    hard_branch_count: int = 6
    max_constraints: int = 3


class TTSInput(BaseModel):
    anon_task_ref: str
    task_description: str
    files_touched: list[str] = []
    error_prone_domains: list[str] = []


def compute_fingerprint_for(config: TTSConfig, input_data: TTSInput) -> str:
    fingerprint_dict = {
        k: getattr(input_data, k)
        for k in config._fingerprint_fields
        if hasattr(input_data, k)
    }
    raw_str = json.dumps(fingerprint_dict, sort_keys=True)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:24]


def evaluate_task_complexity(
    task_description: str,
    *,
    files_touched: list[str],
    error_prone_domains: list[str],
) -> str:
    """纯判定: 多文件修改或命中高频报错领域 → HARD_TASK。"""
    if len(files_touched) > 1:
        return "HARD_TASK"
    desc = task_description.lower()
    for domain in error_prone_domains:
        if domain.lower() in desc:
            return "HARD_TASK"
    return "NORMAL_TASK"


async def _write_trail_async(output_dir: Path, trail: list) -> None:
    trail_path = output_dir / "decision_trail.json"
    with open(trail_path, "w") as f:
        json.dump(trail, f, indent=2)


async def tts_scale_workflow(
    config: TTSConfig,
    input_data: TTSInput,
    output_dir: Path,
    *,
    memory: MemoryBank,
    on_step: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """业务事务: 复杂度评估 → 算力缩放决策 → 避坑规则检索。

    Composes oskill: [memory_induction 的产物经 memory 检索]
    """
    decision_trail = []
    status = "completed"
    error = None
    findings = {}

    try:
        if on_step:
            on_step({"step": 1, "msg": "evaluate complexity"})

        difficulty = evaluate_task_complexity(
            input_data.task_description,
            files_touched=input_data.files_touched,
            error_prone_domains=input_data.error_prone_domains,
        )
        branch_count = (
            config.hard_branch_count
            if difficulty == "HARD_TASK"
            else config.normal_branch_count
        )

        if on_step:
            on_step({"step": 2, "msg": "retrieve memory constraints"})

        # 历史反思经验 → 硬约束注入
        memories = await memory.search_experience(
            input_data.task_description, top_k=config.max_constraints
        )
        constraints = [
            f"[经验 {m.memory_id}] {m.successful_strategy.get('reason', '')}"
            for m in memories
        ]

        findings = {
            "difficulty": difficulty,
            "branch_count": branch_count,
            "constraints": constraints,
            "scaled_up": difficulty == "HARD_TASK",
        }
        decision_trail.append(
            {"action": "scale", "difficulty": difficulty, "branches": branch_count}
        )

        if on_step:
            on_step({"step": 3, "msg": "scale decision done"})

    except asyncio.CancelledError:
        status = "cancelled"
        error = {"reason": "Task was cancelled or timed out"}
        decision_trail.append({"action": "cancelled"})
        raise

    except Exception as e:
        status = "failed"
        error = {"type": type(e).__name__, "message": str(e)}
        decision_trail.append({"action": "error", "error": error})

    finally:
        if "decision_trail" in config._enabled_pillars:
            output_dir.mkdir(parents=True, exist_ok=True)
            await asyncio.shield(_write_trail_async(output_dir, decision_trail))

    return {
        "status": status,
        "error": error,
        "findings": findings,
        "fingerprint": compute_fingerprint_for(config, input_data)
        if "fingerprint" in config._enabled_pillars
        else None,
        "cost_usd": 0.0,
    }
