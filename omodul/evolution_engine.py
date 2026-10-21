"""3O 范式内化 — OpenRSI MCTS 演化搜索引擎 (omodul).

evo_draft 生成 N 分支 → 沙盒测试 → 全败 Debug / 部分过 Improve /
互补高分 Crossover → 深度 D 或 100% 通过率停止。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from obase import OmodulBaseConfig
from obase.sandbox_runner import SandboxResult
from oprim import parse_test_output
from oskill import evo_crossover, evo_debug, evo_draft, evo_improve
from oskill.protocols import LLMCaller
from pydantic import BaseModel


class EvolutionConfig(OmodulBaseConfig):
    _omodul_name = "evolution_engine"
    _omodul_version = "1.0.0"
    _enabled_pillars = {"decision_trail", "fingerprint"}
    _fingerprint_fields = {"anon_task_ref", "task_id"}

    branch_count: int = 3
    max_depth: int = 5
    max_sandbox_calls: int = 20
    timeout_sec: int = 30
    test_command: str = "python -m pytest -q"


class EvolutionInput(BaseModel):
    anon_task_ref: str
    task_id: str
    requirement: str
    test_command: str | None = None


def compute_fingerprint_for(config: EvolutionConfig, input_data: EvolutionInput) -> str:
    fingerprint_dict = {
        k: getattr(input_data, k)
        for k in config._fingerprint_fields
        if hasattr(input_data, k)
    }
    raw_str = json.dumps(fingerprint_dict, sort_keys=True)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:24]


async def _write_trail_async(output_dir: Path, trail: list) -> None:
    trail_path = output_dir / "decision_trail.json"
    with open(trail_path, "w") as f:
        json.dump(trail, f, indent=2)


async def run_evolution_workflow(
    config: EvolutionConfig,
    input_data: EvolutionInput,
    output_dir: Path,
    *,
    llm: LLMCaller,
    sandbox: Callable[[str, str, int], Awaitable[SandboxResult]],
    on_step: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """业务事务: 多分支 MCTS 演化搜索 (沙盒反馈驱动)。

    Composes oskill: [evo_draft, evo_debug, evo_improve, evo_crossover]
    Composes oprim: [parse_test_output]
    """
    decision_trail = []
    status = "completed"
    error = None
    findings = {}
    trail_log: list[dict[str, Any]] = []

    branches: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    sandbox_calls = 0
    test_command = input_data.test_command or config.test_command

    try:
        if on_step:
            on_step({"step": 1, "msg": f"draft {config.branch_count} branches"})

        # 1. 初始分支 (N 个独立 Draft)
        for i in range(config.branch_count):
            code = await evo_draft(input_data.requirement, llm=llm)
            branches.append({"branch_id": f"branch_{i}", "code": code, "score": 0.0})

        depth = 0
        while depth < config.max_depth and sandbox_calls < config.max_sandbox_calls:
            depth += 1
            evaluated: list[dict[str, Any]] = []

            for branch in branches:
                # 2. 落盘分支代码 + 沙盒测试
                branch_dir = output_dir / "branches" / branch["branch_id"]
                branch_dir.mkdir(parents=True, exist_ok=True)
                (branch_dir / "solution.py").write_text(branch["code"], encoding="utf-8")

                result = await sandbox(branch_dir.as_posix(), test_command, config.timeout_sec)
                sandbox_calls += 1

                parsed = parse_test_output(result.stdout + "\n" + result.stderr)
                total = parsed["tests_passed"] + parsed["tests_failed"]
                score = parsed["tests_passed"] / total if total else 0.0
                branch["score"] = round(score, 4)
                branch["result"] = parsed
                evaluated.append(branch)
                trail_log.append(
                    {
                        "task_id": input_data.task_id,
                        "branch_id": branch["branch_id"],
                        "operator_used": "sandbox_eval",
                        "input_code": branch["code"][:500],
                        "error_trace": "\n".join(parsed["errors"][:3]),
                        "sandbox_result": {
                            "exit_code": result.exit_code,
                            "tests_passed": parsed["tests_passed"],
                            "tests_failed": parsed["tests_failed"],
                            "execution_time_ms": result.execution_time_ms,
                        },
                    }
                )

            evaluated.sort(key=lambda b: b["score"], reverse=True)
            best = evaluated[0]

            if on_step:
                on_step({"step": 2, "msg": f"depth {depth}: best={best['score']}"})

            # 3. 停止条件: 100% 通过
            if best["score"] >= 1.0:
                break

            # 4. 演化策略分派
            new_code: str | None = None
            operator = ""
            if best["score"] == 0.0 and not best["result"]["errors"]:
                pass  # 无错误信息, 保持分支
            elif best["score"] == 0.0:
                # 全败 → Debug
                operator = "Evo_Debug"
                new_code = await evo_debug(
                    best["code"], error_trace="\n".join(best["result"]["errors"]), llm=llm
                )
            elif best["score"] < 1.0 and len(evaluated) >= 2:
                # 部分通过 → Improve 最优分支; 存在互补高分对 → Crossover
                if evaluated[1]["score"] > 0:
                    operator = "Evo_Crossover"
                    new_code = await evo_crossover(
                        best["code"], code_b=evaluated[1]["code"], llm=llm
                    )
                else:
                    operator = "Evo_Improve"
                    new_code = await evo_improve(
                        best["code"],
                        feedback=f"tests passed: {parsed['tests_passed']}, "
                        f"failed: {parsed['tests_failed']}\n"
                        + "\n".join(best["result"]["errors"][:3]),
                        llm=llm,
                    )

            if new_code is not None and operator:
                new_branch = {
                    "branch_id": f"branch_{len(branches)}",
                    "code": new_code,
                    "score": 0.0,
                }
                branches.append(new_branch)
                trail_log.append(
                    {
                        "task_id": input_data.task_id,
                        "branch_id": new_branch["branch_id"],
                        "operator_used": operator,
                        "input_code": (best["code"] if operator != "Evo_Debug" else "")[:500],
                        "error_trace": "\n".join(best["result"]["errors"][:3]),
                        "output_code": new_code[:500],
                    }
                )
                decision_trail.append(
                    {"action": "evolve", "operator": operator, "depth": depth}
                )

        findings = {
            "best_code": (best or {}).get("code", ""),
            "best_score": (best or {}).get("score", 0.0),
            "branches_explored": len(branches),
            "sandbox_calls": sandbox_calls,
            "depth_reached": depth,
            "trajectories": trail_log,
        }
        decision_trail.append(
            {"action": "done", "best_score": findings["best_score"], "calls": sandbox_calls}
        )

        if on_step:
            on_step({"step": 3, "msg": "evolution done"})

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
