"""omodul.long_task_memory_workflow — 长任务跨会话记忆压缩与快照恢复事务.

集成：oskill.context_compression（长文本摘要）+ obase.versionstore.VersionStore
（快照持久化）+ oprim.file_read_slice / file_write_atomic（读写）。

Pillars: fingerprint, decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

MemoryAction = Literal["compress", "snapshot", "restore"]


class LongTaskMemoryConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "long_task_memory_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"task_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    budget_tokens: int = 4000
    snapshot_root: str | None = None  # 缺省 output_dir/versionstore


class LongTaskMemoryInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str
    action: MemoryAction = "compress"
    memory_text: str = ""          # compress / snapshot 时提供
    memory_path: str | None = None  # snapshot 时提供（文件/目录）
    caller: Any = None             # oskill.context_compression 的 LLM 注入
    snapshot_rev: str | None = None  # restore 时提供


def compute_fingerprint_for(config: LongTaskMemoryConfig, input_data: LongTaskMemoryInput) -> str:
    """Fingerprint over task_id + action。"""
    return compute_fingerprint({"task_id": input_data.task_id, "action": input_data.action})


async def long_task_memory_workflow(
    config: LongTaskMemoryConfig,
    input_data: LongTaskMemoryInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """跨会话记忆压缩 / 快照 / 恢复事务。失败绝不 raise。"""
    trail = Trail()
    fp = compute_fingerprint_for(config, input_data)

    try:
        from obase.versionstore import VersionStore

        store = VersionStore(config.snapshot_root or Path(output_dir) / "versionstore")

        if input_data.action == "compress":
            if not input_data.memory_text:
                raise ValueError("compress action requires memory_text")
            if input_data.caller is None:
                raise ValueError("compress action requires an injected caller (LLM)")
            from oskill import context_compression

            if on_step:
                on_step({"stage": "compress", "status": "running"})
            result = await context_compression(
                input_data.memory_text,
                caller=input_data.caller,
                budget_tokens=config.budget_tokens,
            )
            trail.record(
                event="memory_compressed",
                task_id=input_data.task_id,
                original_chars=result["original_chars"],
                ratio=result["ratio"],
            )
            return build_result(
                status="completed",
                error=None,
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(Path(output_dir)) if output_dir else None,
                **result,
            )

        if input_data.action == "snapshot":
            if input_data.memory_path is None:
                raise ValueError("snapshot action requires memory_path")
            rev = store.snapshot([input_data.memory_path], message=f"task:{input_data.task_id}")
            trail.record(event="memory_snapshot", task_id=input_data.task_id, rev=rev)
            return build_result(
                status="completed",
                error=None,
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(Path(output_dir)) if output_dir else None,
                rev=rev,
                snapshots=len(store.list_versions()),
            )

        if input_data.action == "restore":
            if input_data.snapshot_rev is None:
                raise ValueError("restore action requires snapshot_rev")
            if input_data.memory_path is None:
                raise ValueError("restore action requires memory_path (target)")
            store.restore(input_data.snapshot_rev, target_root=input_data.memory_path)
            trail.record(
                event="memory_restored", task_id=input_data.task_id,
                rev=input_data.snapshot_rev,
            )
            return build_result(
                status="completed",
                error=None,
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(Path(output_dir)) if output_dir else None,
                restored_rev=input_data.snapshot_rev,
            )

        raise ValueError(f"unknown action: {input_data.action}")

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
        )
