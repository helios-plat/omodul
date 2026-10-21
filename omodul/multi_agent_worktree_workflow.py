"""omodul.multi_agent_worktree_workflow — 多 Agent 基于 Git Worktree 隔离的代码并行修改事务.

集成：oprim.git_worktree_create / git_worktree_remove（隔离文件树）
+ 注入的 agent runner（每任务执行）+ 报告汇总。

Pillars: fingerprint, decision_trail, report, cost
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import (
    BaseConfig,
    CostTracker,
    Trail,
    build_result,
    compute_fingerprint,
    write_report,
)


class MultiAgentWorktreeConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "multi_agent_worktree_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"repo_path"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report", "cost"}

    max_parallel: int = 4
    worktree_root: str | None = None  # 缺省 "<repo>-worktrees"
    report_format: str = "markdown"
    cleanup_worktrees: bool = True     # 完成后是否回收 worktree


class MultiAgentWorktreeInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    repo_path: str
    tasks: list[dict] = []            # [{"branch": str, "description": str, "payload": dict}]
    agent_runner: Callable[..., Any] | None = None  # async (worktree_path, task) -> dict
    timeout_s: float = 600.0


def compute_fingerprint_for(
    config: MultiAgentWorktreeConfig, input_data: MultiAgentWorktreeInput
) -> str:
    """Fingerprint over repo_path + task branches。"""
    return compute_fingerprint(
        {
            "repo_path": input_data.repo_path,
            "branches": [t.get("branch", "") for t in input_data.tasks],
        }
    )


async def multi_agent_worktree_workflow(
    config: MultiAgentWorktreeConfig,
    input_data: MultiAgentWorktreeInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """并行 Agent 各自在独立 worktree 修改代码，汇总结果。失败绝不 raise。"""
    trail = Trail()
    cost = CostTracker()
    fp = compute_fingerprint_for(config, input_data)
    t0 = time.time()

    try:
        import asyncio

        from oprim import git_worktree_create, git_worktree_remove

        repo = Path(input_data.repo_path)
        if not repo.is_dir():
            raise ValueError(f"repo_path not found: {repo}")
        if not input_data.tasks:
            raise ValueError("tasks must not be empty")
        if input_data.agent_runner is None:
            raise ValueError("agent_runner (async callable) is required")

        async def run_task(task: dict) -> dict:
            branch = task.get("branch", "")
            if not branch:
                return {"branch": "", "status": "failed", "error": "branch required"}
            wt: Path | None = None
            try:
                wt = await git_worktree_create(
                    branch,
                    repo=repo,
                    path=Path(config.worktree_root) if config.worktree_root else None,
                    create_branch=True,
                )
                if on_step:
                    on_step({"stage": "worktree", "branch": branch, "path": str(wt)})
                outcome = await input_data.agent_runner(str(wt), task)
                if not isinstance(outcome, dict):
                    outcome = {"status": "completed", "result": outcome}
                outcome.setdefault("status", "completed")
                return {"branch": branch, "worktree": str(wt), **outcome}
            except Exception as exc:
                return {
                    "branch": branch,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            finally:
                if wt is not None and config.cleanup_worktrees:
                    try:
                        await git_worktree_remove(str(wt), repo=repo, force=True)
                    except Exception:  # noqa: BLE001 - 清理失败不致命
                        pass

        sem = asyncio.Semaphore(max(1, config.max_parallel))

        async def guarded(task: dict) -> dict:
            async with sem:
                return await asyncio.wait_for(run_task(task), timeout=input_data.timeout_s)

        results = await asyncio.gather(*(guarded(t) for t in input_data.tasks))
        succeeded = sum(1 for r in results if r.get("status") == "completed")

        trail.record(
            event="worktrees_finished", total=len(results), succeeded=succeeded,
        )

        report = _render_report(repo, results, time.time() - t0)
        report_path = write_report(
            report, output_dir=Path(output_dir), name="multi_agent_worktree_report",
            fmt=config.report_format,
        )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            report_path=report_path,
            cost_usd=round(cost.total_usd, 6),
            findings={"total": len(results), "succeeded": succeeded, "results": results},
        )

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            cost_usd=round(cost.total_usd, 6),
        )


def _render_report(repo: Path, results: list[dict], elapsed: float) -> str:
    lines = [
        "# 多 Agent Worktree 并行修改报告",
        "",
        f"- 仓库: `{repo}`",
        f"- 任务数: {len(results)} / 成功: "
        f"{sum(1 for r in results if r.get('status') == 'completed')}",
        f"- 耗时: {elapsed:.1f}s",
        "",
    ]
    for r in results:
        lines.append(
            f"## {r.get('branch') or '(no branch)'} — {r.get('status')}\n"
            f"- worktree: `{r.get('worktree') or '-'}`"
        )
        if r.get("error"):
            lines.append(f"- error: {r['error']}")
        if isinstance(r.get("result"), str):
            lines.append(f"- result: {r['result'][:500]}")
        lines.append("")
    return "\n".join(lines)
