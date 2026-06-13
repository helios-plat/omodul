"""Auto-split from hicode whl."""

from __future__ import annotations
from oskill import apply_edit_block
from oprim import file_read, file_write
import json
import re
import sys
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel
from ._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, llm_call, write_report

class RefactorTransactionConfig(BaseConfig):
    max_files: int = 20
    max_file_tokens: int = 5000
    dry_run: bool = False
    _omodul_name: ClassVar[str] = 'refactor_transaction'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'instruction', 'paths'}
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'fingerprint', 'cost', 'report'}

class RefactorTransactionInput(BaseModel):
    instruction: str
    paths: list[str]
    caller: Any
    context: str = ''

class RunAndFixConfig(BaseConfig):
    max_iterations: int = 5
    timeout: int = 60
    _omodul_name: ClassVar[str] = 'run_and_fix'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'command'}
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'cost', 'report'}

class RunAndFixInput(BaseModel):
    command: str
    cwd: str
    caller: Any
    target_files: list[str] = []

class MigrateDependencyConfig(BaseConfig):
    max_files: int = 50
    max_file_tokens: int = 3000
    _omodul_name: ClassVar[str] = 'migrate_dependency'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'dependency', 'target_version'}
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'fingerprint', 'cost', 'report'}

class MigrateDependencyInput(BaseModel):
    dependency: str
    target_version: str
    root_path: str
    caller: Any

class CreateCheckpointConfig(BaseConfig):
    _omodul_name: ClassVar[str] = 'create_checkpoint'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'session_id', 'message_count'}
    _enabled_pillars: ClassVar[set[str]] = {'fingerprint', 'decision_trail'}

class CreateCheckpointInput(BaseModel):
    messages: list[dict]
    session_id: str = ''
    metadata: dict = {}
    store: Any = None

class RewindConfig(BaseConfig):
    _omodul_name: ClassVar[str] = 'rewind_to_checkpoint'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = set()
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail'}

class RewindInput(BaseModel):
    checkpoint_id: str
    store: Any = None
    checkpoint_path: str = ''

class CompactConversationConfig(BaseConfig):
    target_budget: int = 4000
    _omodul_name: ClassVar[str] = 'compact_conversation'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'message_count', 'last_msg_hash'}
    _enabled_pillars: ClassVar[set[str]] = {'cost', 'decision_trail', 'fingerprint'}

class CompactConversationInput(BaseModel):
    messages: list[dict]
    caller: Any
    session_id: str = ''

class InstallPluginConfig(BaseConfig):
    _omodul_name: ClassVar[str] = 'install_plugin'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'plugin_name', 'version'}
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'fingerprint'}

class InstallPluginInput(BaseModel):
    plugin_bundle: dict
    install_dir: str

async def refactor_transaction(
    config: RefactorTransactionConfig,
    input_data: RefactorTransactionInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """多文件重构事务：LLM 生成编辑指令 → 批量应用 → 失败整批回滚。

    支柱：decision_trail + fingerprint + cost + report
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    cost = CostTracker()
    status = "completed"
    error = None
    report_path = None
    applied: list[str] = []
    snapshots: dict[str, str] = {}   # path → original content（简化 versionstore）

    fingerprint = compute_fingerprint({
        "instruction": input_data.instruction,
        "paths": sorted(input_data.paths),
    })

    try:
        trail.record(event="refactor_start", instruction=input_data.instruction)
        if on_step:
            on_step({"event": "refactor_start"})

        # 读取所有文件并打快照
        file_contents: dict[str, str] = {}
        for path in input_data.paths[:config.max_files]:
            try:
                content = file_read(path)
                file_contents[path] = content
                snapshots[path] = content  # 快照
            except Exception as e:  # pragma: no cover
                trail.record(event="file_read_error", path=path, error=str(e))  # pragma: no cover

        if not file_contents:
            raise ValueError("no files could be read")

        # 构建上下文
        code_ctx = "\n\n".join(
            f"## {p}\n```\n{c[:config.max_file_tokens * 4]}\n```"
            for p, c in list(file_contents.items())[:8]
        )

        prompt = (
            f"Perform this refactoring across the codebase:\n{input_data.instruction}\n\n"
            f"{'Context: ' + input_data.context if input_data.context else ''}\n\n"
            f"Return a JSON array of edits:\n"
            f'[{{"path": "file.py", "search": "old code", "replace": "new code"}}, ...]\n\n'
            f"Return ONLY the JSON array.\n\n{code_ctx}"
        )

        response = await llm_call(
            [{"role": "user", "content": prompt}],
            caller=input_data.caller, cost=cost, trail=trail,
            model=config.llm_model, event="generate_edits",
        )
        raw = extract_text(response)
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'```\s*$', '', raw, flags=re.MULTILINE).strip()

        try:
            edits = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r'\[.*?\]', raw, re.DOTALL)
            edits = json.loads(m.group(0)) if m else []

        trail.record(event="edits_parsed", count=len(edits))

        # 应用编辑（dry_run 时跳过写盘）
        failed_paths: list[str] = []
        for edit in edits:
            path = edit.get("path", "")
            if path not in file_contents:
                continue  # pragma: no cover
            search = edit.get("search", "")
            replace = edit.get("replace", "")
            if not search:
                continue  # pragma: no cover

            result = apply_edit_block(
                file_contents[path],
                blocks=[EditBlock(search, replace)],
            )
            if result.ok:
                file_contents[path] = result.content
                if not config.dry_run:
                    file_write(path, content=result.content)
                applied.append(path)
                trail.record(event="edit_applied", path=path)
            else:
                failed_paths.append(path)  # pragma: no cover
                trail.record(event="edit_failed", path=path, conflicts=result.conflicts)  # pragma: no cover

        if failed_paths:
            # 回滚已写的文件
            for p in applied:  # pragma: no cover
                if p in snapshots:  # pragma: no cover
                    file_write(p, content=snapshots[p])  # pragma: no cover
            raise ValueError(f"edits failed for: {failed_paths}")  # pragma: no cover

        report_content = (
            f"# Refactor Transaction Report\n\n"
            f"**Instruction**: {input_data.instruction}\n"
            f"**Applied to**: {len(applied)} files\n"
            f"**Dry run**: {config.dry_run}\n\n"
            f"## Files Modified\n" + "\n".join(f"- {p}" for p in applied)
        )
        report_path = write_report(report_content, output_dir=output_dir, name="refactor")
        trail.record(event="completed", applied=applied)
        if on_step:
            on_step({"event": "completed", "applied": applied})

    except Exception as exc:
        status = "failed"
        error = {"type": type(exc).__name__, "message": str(exc)}
        trail.record(event="error", error=error)
        # 最大努力回滚
        for p, orig in snapshots.items():
            try:  # pragma: no cover
                file_write(p, content=orig)  # pragma: no cover
            except Exception:  # pragma: no cover
                pass  # pragma: no cover
    finally:
        trail_path = trail.write(output_dir)

    return build_result(
        status=status, error=error, fingerprint=fingerprint,
        trail=trail, trail_path=trail_path, report_path=report_path,
        cost_usd=cost.total_usd, applied=applied,
    )
