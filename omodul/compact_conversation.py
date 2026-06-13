"""Auto-split from hicode whl."""

from __future__ import annotations
import json
import re
import sys
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel
from ._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, llm_call, write_report

class CompactConfig(BaseConfig):
    max_files: int = 20
    max_file_tokens: int = 5000
    dry_run: bool = False
    _omodul_name: ClassVar[str] = 'refactor_transaction'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'instruction', 'paths'}
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'fingerprint', 'cost', 'report'}

class CompactInput(BaseModel):
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

async def compact_conversation(
    config: CompactConversationConfig,
    input_data: CompactConversationInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """自动压缩会话上下文，写摘要 checkpoint。

    支柱：cost + decision_trail + fingerprint
    M5 Owner 裁决：加 fingerprint 区分多次压缩版本。
    """
    import hashlib
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    cost = CostTracker()
    status = "completed"
    error = None

    last_msg_text = str(input_data.messages[-1] if input_data.messages else {})
    last_hash = hashlib.sha256(last_msg_text.encode()).hexdigest()[:8]
    fingerprint = compute_fingerprint({
        "message_count": len(input_data.messages),
        "last_msg_hash": last_hash,
        "session_id": input_data.session_id,
    })

    compacted_messages = list(input_data.messages)

    try:
        trail.record(event="compact_start", n_messages=len(input_data.messages))
        if on_step:
            on_step({"event": "compact_start"})

        if len(input_data.messages) <= 4:
            trail.record(event="no_compact_needed")
            return build_result(
                status="completed", error=None, fingerprint=fingerprint,
                trail=trail, trail_path=trail.write(output_dir),
                cost_usd=0.0, messages=compacted_messages,
                compacted=False,
            )

        keep_first = input_data.messages[:1]
        keep_last = input_data.messages[-2:]
        middle = input_data.messages[1:-2]

        history = "\n".join(
            f"[{m.get('role','?')}]: {_msg_text(m)[:400]}" for m in middle
        )

        response = await llm_call(
            [{"role": "user", "content":
              f"Summarize this conversation history in 3-5 sentences, preserving key technical decisions:\n\n{history[:6000]}"}],
            caller=input_data.caller, cost=cost, trail=trail,
            model=config.llm_model, event="compact_llm",
        )
        summary = extract_text(response).strip()
        summary_msg = {"role": "user", "content": f"[Summary]: {summary}"}
        compacted_messages = keep_first + [summary_msg] + keep_last

        trail.record(event="compact_done",
                     before=len(input_data.messages), after=len(compacted_messages))
        if on_step:
            on_step({"event": "compact_done"})

    except Exception as exc:
        status = "failed"
        error = {"type": type(exc).__name__, "message": str(exc)}
        trail.record(event="error", error=error)
    finally:
        trail_path = trail.write(output_dir)

    return build_result(
        status=status, error=error, fingerprint=fingerprint,
        trail=trail, trail_path=trail_path,
        cost_usd=cost.total_usd,
        messages=compacted_messages,
        compacted=status == "completed" and len(input_data.messages) > 4,
    )
