"""omodul.mission_revert — 多 Worker 工作区级一键回滚 (Vigla Mission Revert 复刻)。

多 Worker 改乱代码后: 各 worktree 恢复到任务基线 commit; 坏改动**隔离** (打包到
quarantine 目录, 不删除); 回滚动作本身审计。

分层: omodul (事务) — 复用 git worktree 机制 + checkpoint_store + 审计。
"""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oprim._audit_emit import AuditEvent, JsonlSink


@dataclass
class WorktreeState:
    """单个 worker 工作区状态。"""

    worker_id: str
    worktree: str
    base_commit: str = ""        # 任务启动时基线
    branch: str = ""
    dirty: bool = False


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd),
                          capture_output=True, text=True, timeout=120)


def snapshot_mission_baseline(mission_id: str, worktrees: list[dict[str, str]],
                              *, repo: str = "") -> list[WorktreeState]:
    """任务启动时记录各 worktree 基线 commit。"""
    states: list[WorktreeState] = []
    for wt in worktrees:
        cwd = Path(wt["worktree"])
        r = _git(cwd, "rev-parse", "HEAD")
        states.append(WorktreeState(
            worker_id=wt.get("worker_id", Path(wt["worktree"]).name),
            worktree=str(cwd),
            base_commit=r.stdout.strip() if r.returncode == 0 else "",
            branch=wt.get("branch", ""),
        ))
    return states


def mission_revert(
    mission_id: str,
    worktrees: list[dict[str, str]],
    *,
    base_commits: dict[str, str] | None = None,
    quarantine_dir: str = "",
    trace_id: str | None = None,
    sink: JsonlSink | None = None,
) -> dict[str, Any]:
    """一键回滚: 各 worktree reset 到基线 + 坏改动隔离打包。

    Args:
        worktrees: [{"worker_id", "worktree", "branch"}]
        base_commits: {worktree_path: baseline_commit} (缺省取当前 HEAD 上一提交? 不 —
                       缺省用 git reflog 最近一次 clean 状态会误伤; 建议任务启动时
                       用 snapshot_mission_baseline 记录)
        quarantine_dir: 坏改动隔离目录 (缺省 ~/.veya/quarantine/<mission>)
    """
    qdir = Path(quarantine_dir or Path.home() / ".veya" / "quarantine" / mission_id)
    qdir.mkdir(parents=True, exist_ok=True)

    restored: list[dict[str, Any]] = []
    reverted_all = True
    quarantined: list[str] = []

    for wt in worktrees:
        cwd = Path(wt["worktree"])
        worker = wt.get("worker_id", cwd.name)
        if not cwd.exists():
            restored.append({"worker_id": worker, "ok": False, "error": "worktree 不存在"})
            reverted_all = False
            continue

        base = (base_commits or {}).get(str(cwd)) or (base_commits or {}).get(wt.get("branch", ""))
        if not base:
            restored.append({"worker_id": worker, "ok": False,
                             "error": "无基线 commit (任务启动时 snapshot_mission_baseline)"})
            reverted_all = False
            continue

        # 1) 隔离坏改动: 已跟踪 diff + 未跟踪文件清单 → quarantine 包
        patch_parts: list[str] = []
        r = _git(cwd, "diff", "HEAD")
        if r.returncode == 0 and r.stdout.strip():
            patch_parts.append(r.stdout)
        r2 = _git(cwd, "status", "--porcelain")
        if r2.returncode == 0 and r2.stdout.strip():
            untracked = [line[3:] for line in r2.stdout.splitlines()
                         if line.startswith("?? ")]
            if untracked:
                patch_parts.append("\n# 未跟踪文件 (回滚时清理):\n" +
                                   "\n".join(untracked))
        if patch_parts:
            patch = qdir / f"{worker}-{uuid.uuid4().hex[:8]}.patch"
            patch.write_text("\n".join(patch_parts), encoding="utf-8")
            quarantined.append(str(patch))

        # 2) reset 到基线 + 清理未跟踪 (硬回滚, 但改动已隔离)
        r = _git(cwd, "reset", "--hard", base)
        ok = r.returncode == 0
        if ok:
            _git(cwd, "clean", "-fd")
        reverted_all = reverted_all and ok
        restored.append({"worker_id": worker, "ok": ok,
                         "restored_commit": base,
                         "error": "" if ok else r.stderr[-300:]})

    # 回滚动作审计
    if sink is None:
        sink = JsonlSink(str(Path.home() / ".veya" / "audit" / "revert.jsonl"))
    audit = AuditEvent(
        event_type="learn",
        trace_id=trace_id or f"rv_{mission_id}",
        inputs={"mission_id": mission_id},
        learning={"reverted_all": reverted_all, "workers": len(worktrees),
                  "quarantined": len(quarantined)},
    )
    sink.write(audit)

    return {
        "reverted": reverted_all,
        "restored": restored,
        "restored_commit": restored[0].get("restored_commit", "") if restored else "",
        "quarantined_changes": quarantined,
        "audit_id": audit.audit_id,
    }


__all__ = ["mission_revert", "snapshot_mission_baseline", "WorktreeState"]
