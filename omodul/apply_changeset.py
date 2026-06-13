"""Auto-split from hicode whl."""

from __future__ import annotations
import sys as _sys
import os as _os
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel

def compute_fingerprint_for(config: ChangesetConfig,
                            input_data: ChangesetInput) -> str:
    """计算 apply_changeset fingerprint，供服务层去重。"""
    fields = {k: getattr(config, k, None)
              for k in sorted(config._fingerprint_fields)}
    payload = {
        "omodul": config._omodul_name,
        "version": config._omodul_version,
        "config": fields,
        "input_hash": hashlib.sha256(
            json.dumps(
                input_data.model_dump() if hasattr(input_data, "model_dump")
                else str(input_data),
                sort_keys=True, default=str,
            ).encode()
        ).hexdigest(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()


# TODO(production): replace with `from obase.versionstore import VersionStore`
# once obase.versionstore exposes a snapshot/restore class interface.
# Current obase.versionstore only exports jsonl_append/read/latest (log-oriented).
@dataclass
class VersionStore:
    """
    obase.versionstore 占位。
    生产版持久化到 .hicode/snapshots/；此处用内存 + 临时目录模拟。
    """
    _snapshots: dict[str, dict[str, str]] = field(default_factory=dict)

    def snapshot(self, paths: list[Path]) -> str:
        """打快照：{rev_id -> {path -> content}}。路径不存在记 None。"""
        rev = str(uuid.uuid4())[:8]
        self._snapshots[rev] = {str(p): p.read_text(encoding='utf-8', errors='replace') if p.exists() else None for p in paths}
        return rev

    def restore(self, rev: str) -> list[str]:
        """还原快照中所有文件，返回已还原路径列表。"""
        snap = self._snapshots.get(rev, {})
        restored = []
        for path_str, content in snap.items():
            p = Path(path_str)
            if content is None:
                if p.exists():
                    p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding='utf-8')
            restored.append(path_str)
        return restored

    def list_revs(self) -> list[str]:
        return list(self._snapshots.keys())

class EditBlock(BaseModel):
    """apply_edit_block 格式：<<<SEARCH / >>>REPLACE 块。"""
    search: str
    replace: str

class Edit(BaseModel):
    """单文件编辑指令。三种格式之一。"""
    path: str
    full_content: str | None = None
    blocks: list[EditBlock] | None = None
    unified_diff: str | None = None
    create_if_missing: bool = True
    validate_syntax: bool = True

class ChangesetConfig(BaseModel):
    """omodul BaseConfig（§5.3 标准）。"""
    sandbox_root: str = ''
    syntax_check_enabled: bool = True
    overwrite: bool = True
    _omodul_name: ClassVar[str] = 'apply_changeset'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'path', 'full_content', 'blocks', 'unified_diff'}
    _enabled_pillars: ClassVar[set[str]] = {'fingerprint', 'decision_trail'}

class ChangesetInput(BaseModel):
    edits: list[Edit]
    versionstore: Any = None
    message: str = ''

def apply_changeset(
    config: ChangesetConfig,
    input_data: ChangesetInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """
    omodul: apply_changeset
    ========================
    Agent 提议的编辑批量落盘事务。

    流程
    ----
    1. fingerprint 计算 → 去重判断（服务层可 skip 重复 changeset）
    2. versionstore 快照（undo 之根）
    3. 逐文件：read → compute_content → validate → write
    4. 任一文件失败 → 整批回滚（restore snapshot）
    5. decision_trail 落盘
    6. 返回 {applied, skipped, failed_paths, snapshot_rev, ...}

    返回结构
    --------
    {
        "applied": list[str],          # 成功写盘的路径
        "skipped": list[str],          # 跳过的路径（校验失败但配置不回滚）
        "failed_paths": list[str],     # 失败路径（触发整批回滚）
        "snapshot_rev": str,           # versionstore 快照 rev（undo 用）
        "fingerprint": str,
        "status": "completed" | "failed" | "rolled_back",
        "error": dict | None,
        "decision_trail": {path, steps, run_id},
        "cost_usd": 0.0,               # 无 LLM，始终 0
    }
    """
    run_id = str(uuid.uuid4())[:8]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    trail: list[dict] = []
    status = "completed"
    error: dict | None = None
    applied: list[str] = []
    skipped: list[str] = []
    failed_paths: list[str] = []
    snapshot_rev: str | None = None

    # ── fingerprint ────────────────────────────────────────────────────────
    fingerprint = compute_fingerprint_for(config, input_data)
    _record_step(trail, step_no=0, event="changeset_start",
                 fingerprint=fingerprint, n_edits=len(input_data.edits),
                 message=input_data.message)
    if on_step:
        on_step({"event": "changeset_start", "fingerprint": fingerprint,
                 "n_edits": len(input_data.edits)})

    try:
        # ── 路径解析（沙箱校验）──────────────────────────────────────────
        resolved: list[tuple[Edit, Path]] = []
        for edit in input_data.edits:
            try:
                p = _safe_path(edit.path, config.sandbox_root)
                resolved.append((edit, p))
            except PermissionError as e:
                failed_paths.append(edit.path)
                _record_step(trail, step_no=len(trail) + 1,
                             event="path_rejected", path=edit.path, reason=str(e))

        if failed_paths:
            status = "failed"
            error = {"type": "SandboxViolation", "paths": failed_paths}
            return _build_result(status, error, applied, skipped, failed_paths,
                                 snapshot_rev, fingerprint, trail, output_dir, run_id)

        # ── versionstore 快照（undo 之根）───────────────────────────────
        vstore: VersionStore = input_data.versionstore or VersionStore()
        paths_to_snap = [p for _, p in resolved]
        snapshot_rev = vstore.snapshot(paths_to_snap)
        _record_step(trail, step_no=len(trail) + 1,
                     event="snapshot_taken", rev=snapshot_rev,
                     paths=[str(p) for p in paths_to_snap])

        # ── 逐文件处理 ───────────────────────────────────────────────────
        need_rollback = False

        for step_idx, (edit, path) in enumerate(resolved):
            step_no = len(trail) + 1

            # oprim: file_read
            original = _file_read(path)

            # oskill: compute_content (apply_edit_block / apply_unified_diff)
            computed = _compute_content(edit, original)
            new_content = computed["content"]
            conflicts = computed["conflicts"]
            rejects = computed["rejects"]

            if new_content is None or conflicts or rejects:
                issue = conflicts or rejects  # pragma: no cover
                _record_step(trail, step_no=step_no, event="edit_failed",  # pragma: no cover
                             path=edit.path, issue=issue)
                failed_paths.append(edit.path)  # pragma: no cover
                need_rollback = True  # pragma: no cover
                if on_step:  # pragma: no cover
                    on_step({"event": "edit_failed", "path": edit.path, "issue": issue})  # pragma: no cover
                break  # 立即停止，下面整批回滚  # pragma: no cover

            # oskill: validate_edit (语法检查)
            syntax_enabled = config.syntax_check_enabled and edit.validate_syntax
            validation = _validate_edit(new_content, path=edit.path, enabled=syntax_enabled)

            if not validation["ok"]:
                _record_step(trail, step_no=step_no, event="validation_failed",
                             path=edit.path, errors=validation["errors"])
                failed_paths.append(edit.path)
                need_rollback = True
                if on_step:
                    on_step({"event": "validation_failed",  # pragma: no cover
                             "path": edit.path, "errors": validation["errors"]})
                break

            # oprim: file_write
            _file_write(path, content=new_content)
            applied.append(edit.path)

            _record_step(trail, step_no=step_no, event="file_written",
                         path=edit.path,
                         size_before=len(original),
                         size_after=len(new_content),
                         syntax_ok=validation["ok"])
            if on_step:
                on_step({"event": "file_written", "path": edit.path,
                         "idx": step_idx + 1, "total": len(resolved)})

        # ── 回滚（任一失败）─────────────────────────────────────────────
        if need_rollback:
            restored = vstore.restore(snapshot_rev)
            status = "rolled_back"
            error = {
                "type": "ChangesetRollback",
                "failed_paths": failed_paths,
                "restored": restored,
                "snapshot_rev": snapshot_rev,
            }
            applied = []  # 全部回滚，没有 applied
            _record_step(trail, step_no=len(trail) + 1,
                         event="rollback_complete", restored=restored)
            if on_step:
                on_step({"event": "rollback_complete", "restored": restored})  # pragma: no cover

        _record_step(trail, step_no=len(trail) + 1, event="changeset_done",
                     status=status, applied=len(applied))

    except Exception as exc:  # pragma: no cover
        status = "failed"  # pragma: no cover
        error = {"type": type(exc).__name__, "message": str(exc)}  # pragma: no cover
        _record_step(trail, step_no=len(trail) + 1, event="unexpected_error", error=error)  # pragma: no cover
        # 尝试回滚（最大努力）
        if snapshot_rev and input_data.versionstore:  # pragma: no cover
            try:  # pragma: no cover
                input_data.versionstore.restore(snapshot_rev)  # pragma: no cover
            except Exception:  # pragma: no cover
                pass  # pragma: no cover

    finally:
        _write_trail(trail, output_dir, run_id)

    return _build_result(status, error, applied, skipped, failed_paths,
                         snapshot_rev, fingerprint, trail, output_dir, run_id)
