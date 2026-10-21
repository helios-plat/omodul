"""omodul: 跨模态数据同步事务 (Cross-Modal Synesthesia).

Pillars: fingerprint, decision_trail, report

签名: (config, input_data, output_dir, *, on_step=None)
失败绝对不 raise — 返回 status="failed".

编排共享内存的申请、零拷贝读写与生命周期释放。
用于多模态 Agent 间的高带宽低延迟通信（视觉帧/音频块/张量）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, write_report


class Config(BaseConfig):
    """Cross-Modal Sync Transaction 配置."""

    _omodul_name: ClassVar[str] = "cross_modal_sync_transaction"
    _omodul_version: ClassVar[str] = "1.1.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}

    allocator: Any = None       # obase.PosixShmAllocator
    rw_op: Any = None           # oprim._mmap_zero_copy_rw
    stride_skill: Any = None    # oskill._buffer_stride_alignment


class InputData(BaseModel):
    """Cross-Modal Sync Transaction 输入."""

    shm_name: str = "v_tensor_0"
    action: str = ""            # allocate | write | read | cleanup
    shape: tuple[int, ...] = (1,)
    dtype_size: int = 1
    data: bytes | None = None
    offset: int = 0
    read_size: int = 0

    model_config = {"arbitrary_types_allowed": True}


def cross_modal_sync_transaction(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """执行共享内存申请、读写或清理事务.

    流程:
    1. 计算张量步长与总字节数
    2. 根据 action 执行 allocate / write / read / cleanup
    3. 返回结构化报告

    Args:
        config: 配置（含 allocator / rw_op / stride_skill）
        input_data: 输入数据
        output_dir: 输出目录
        on_step: 步骤回调

    Returns:
        {"status": "completed"|"failed", ...}
    """
    cost = CostTracker()
    trail = Trail()
    decision_trail: list[dict[str, Any]] = []

    allocator = config.allocator
    rw_op = config.rw_op
    stride_skill = config.stride_skill

    shm_name = input_data.shm_name
    action = input_data.action
    shape = input_data.shape
    dtype_size = input_data.dtype_size
    raw_data = input_data.data
    offset = input_data.offset

    try:
        # Phase 1: 计算步长
        trail.record(event="calculate_stride", shape=str(shape))
        if stride_skill is not None:
            stride_info = stride_skill(shape, dtype_size)
        else:
            stride_info = {
                "valid": True,
                "total_bytes": 1024,
                "strides": (1,),
                "shape": shape,
            }

        decision_trail.append({
            "step": "calculate_stride",
            "valid": stride_info["valid"],
            "total_bytes": stride_info.get("total_bytes", 0),
        })

        if not stride_info["valid"]:
            raise ValueError(stride_info.get("error", "Invalid stride"))

        total_size = stride_info["total_bytes"]
        report: dict[str, Any] = {}

        # Phase 2: 执行操作
        if action == "allocate":
            trail.record(event="allocate_shm", name=shm_name, size=total_size)
            if allocator is not None:
                allocator.allocate(shm_name, total_size)
                decision_trail.append({
                    "step": "allocate_shm",
                    "size": total_size,
                    "status": "success",
                })
                report["allocated"] = True
            else:
                report["allocated"] = False
                report["error"] = "No allocator injected"

        elif action == "write":
            trail.record(event="write_shm", name=shm_name)
            if rw_op is not None and raw_data is not None:
                res = rw_op(shm_name, action="write", data=raw_data, offset=offset)
                decision_trail.append({
                    "step": "write_shm",
                    "status": res.get("status"),
                    "bytes": res.get("bytes_written", 0),
                })
                report["write_result"] = {
                    "status": res.get("status"),
                    "bytes": res.get("bytes_written", 0),
                }
            else:
                report["write_result"] = {"status": "error", "error": "No rw_op or data"}

        elif action == "read":
            trail.record(event="read_shm", name=shm_name)
            read_size = input_data.read_size or total_size
            if rw_op is not None:
                res = rw_op(shm_name, action="read", offset=offset, size=read_size)
                decision_trail.append({
                    "step": "read_shm",
                    "status": res.get("status"),
                    "bytes": res.get("bytes_read", 0),
                })
                report["read_result"] = {
                    "status": res.get("status"),
                    "bytes": res.get("bytes_read", 0),
                }
                if "data" in res:
                    report["data"] = res["data"]
            else:
                report["read_result"] = {"status": "error", "error": "No rw_op"}

        elif action == "cleanup":
            trail.record(event="cleanup_shm", name=shm_name)
            if allocator is not None:
                freed = allocator.free(shm_name)
                decision_trail.append({
                    "step": "cleanup_shm",
                    "freed": freed,
                })
                report["freed"] = freed
            else:
                report["freed"] = False

        else:
            report["error"] = f"Unknown action: {action}"

        cost.add(in_tok=100, out_tok=50)

        report_content = (
            f"# Cross-Modal Sync Report\n\n"
            f"## SHM Name: {shm_name}\n"
            f"## Action: {action}\n"
            f"## Total Bytes: {total_size}\n"
            f"## Result: {report}\n"
        )
        report_path = write_report(
            report_content, output_dir=output_dir,
            name=f"cross_modal_{action}_{shm_name}",
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
            summary=f"Cross-modal {action} transaction finished.",
            pillars={
                "fingerprint": {"shm_name": shm_name, "action": action},
                "decision_trail": decision_trail,
                "report": report,
            },
        )

    except Exception as e:
        return build_result(
            status="failed",
            error={"type": type(e).__name__, "message": str(e)},
            cost_usd=cost.total_usd,
            trail=trail,
            summary="Cross-modal transaction crashed.",
            pillars={
                "decision_trail": decision_trail,
                "report": {"error": str(e)},
            },
        )
