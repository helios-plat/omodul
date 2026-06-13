"""Auto-split from hicode whl."""

from __future__ import annotations
from oskill import apply_edit_block
from oprim import file_read, file_write, bash_exec
import json
import re
import sys
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel
from ._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, llm_call, write_report

class RunAndFixConfig(BaseConfig):
    max_files: int = 20
    max_file_tokens: int = 5000
    dry_run: bool = False
    _omodul_name: ClassVar[str] = 'refactor_transaction'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'instruction', 'paths'}
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'fingerprint', 'cost', 'report'}

class RunAndFixInput(BaseModel):
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

async def run_and_fix(
    config: RunAndFixConfig,
    input_data: RunAndFixInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """执行命令，失败时 LLM 修复，有界循环。

    支柱：decision_trail + cost + report
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    cost = CostTracker()
    status = "completed"
    error = None
    report_path = None
    iterations_done = 0

    fingerprint = compute_fingerprint({"command": input_data.command})

    try:
        trail.record(event="run_start", command=input_data.command)

        for iteration in range(config.max_iterations):
            iterations_done = iteration + 1
            if on_step:
                on_step({"event": "iteration", "n": iteration + 1})

            # 执行命令
            result = bash_exec(
                input_data.command,
                cwd=input_data.cwd,
                timeout=config.timeout,
            )
            trail.record(event="exec", iteration=iteration,
                         code=result.code, ok=result.ok,
                         stderr_preview=result.stderr[:200])

            if result.ok:
                trail.record(event="success", iteration=iteration)
                break

            if on_step:
                on_step({"event": "failed", "iteration": iteration, "code": result.code})  # pragma: no cover

            if iteration == config.max_iterations - 1:
                raise ValueError(f"command failed after {config.max_iterations} attempts")

            # 读取目标文件，请求 LLM 修复
            file_parts = []
            for path in input_data.target_files[:5]:
                try:  # pragma: no cover
                    content = file_read(path)  # pragma: no cover
                    file_parts.append(f"## {path}\n```\n{content[:3000]}\n```")  # pragma: no cover
                except Exception:  # pragma: no cover
                    pass  # pragma: no cover

            files_ctx = "\n\n".join(file_parts)
            prompt = (
                f"This command failed:\n```\n{input_data.command}\n```\n\n"
                f"Error output:\n```\n{result.stderr[:2000]}\n{result.stdout[-1000:]}\n```\n\n"
                f"{'Files:\n' + files_ctx if files_ctx else ''}\n\n"
                f"Return JSON edits to fix the error:\n"
                f'[{{"path": "file.py", "search": "old", "replace": "new"}}]\n'
                f"Return ONLY the JSON array."
            )

            response = await llm_call(
                [{"role": "user", "content": prompt}],
                caller=input_data.caller, cost=cost, trail=trail,
                model=config.llm_model, event=f"fix_iteration_{iteration}",
            )
            raw = re.sub(r'```(?:json)?\s*', '', extract_text(response)).strip()
            try:
                edits = json.loads(raw)
            except Exception:  # pragma: no cover
                m = re.search(r'\[.*?\]', raw, re.DOTALL)  # pragma: no cover
                edits = json.loads(m.group(0)) if m else []  # pragma: no cover

            for edit in edits:
                path = edit.get("path", "")  # pragma: no cover
                if not path:  # pragma: no cover
                    continue  # pragma: no cover
                try:  # pragma: no cover
                    content = file_read(path)  # pragma: no cover
                    fixed = apply_edit_block(  # pragma: no cover
                        content,
                        blocks=[EditBlock(edit.get("search", ""), edit.get("replace", ""))],
                    )
                    if fixed.ok:  # pragma: no cover
                        file_write(path, content=fixed.content)  # pragma: no cover
                        trail.record(event="fix_applied", path=path, iteration=iteration)  # pragma: no cover
                except Exception as e:  # pragma: no cover
                    trail.record(event="fix_error", path=path, error=str(e))  # pragma: no cover

        report_content = (
            f"# Run-and-Fix Report\n\n"
            f"**Command**: `{input_data.command}`\n"
            f"**Iterations**: {iterations_done}\n"
            f"**Final status**: {status}\n"
        )
        report_path = write_report(report_content, output_dir=output_dir, name="run_and_fix")

    except Exception as exc:
        status = "failed"
        error = {"type": type(exc).__name__, "message": str(exc)}
        trail.record(event="error", error=error)
    finally:
        trail_path = trail.write(output_dir)

    return build_result(
        status=status, error=error, fingerprint=fingerprint,
        trail=trail, trail_path=trail_path, report_path=report_path,
        cost_usd=cost.total_usd, iterations=iterations_done,
    )
