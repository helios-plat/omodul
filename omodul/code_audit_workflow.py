"""3O 范式 Phase 4 — omodul 业务事务: 代码审计流水线.

将 oprim 原子与 oskill 技能组装为端到端业务事务,
负责防重指纹、决策轨迹审计与成本追踪三大支柱 (规范 §5.3)。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Import the config type from its defining module. Cost-tracker symbols are
# resolved inside the workflow because this module can be imported while OBase
# is still initializing through a cross-package Omodul capability import.
from obase.omodul_base_config import OmodulBaseConfig
from pydantic import BaseModel


class AuditConfig(OmodulBaseConfig):
    _omodul_name = "code_audit_workflow"
    _omodul_version = "1.0.0"

    # 强制启用三大支柱: 去重指纹、决策轨迹审计、成本追踪
    _enabled_pillars = {"fingerprint", "decision_trail", "cost"}

    # 指纹字段，用于同一请求的幂等去重 (禁含 user_id)
    _fingerprint_fields = {"repo_url", "commit_hash", "target_file"}


class AuditInput(BaseModel):
    repo_url: str
    commit_hash: str
    target_file: str
    # runtime_refs 可以携带由服务层(oservi)生成的匿名脱敏引用
    anon_user_ref: str


def compute_fingerprint_for(config: AuditConfig, input_data: AuditInput) -> str:
    """暴露给服务层用于防重/幂等判断 (规范 §5.5)"""
    fingerprint_dict = {
        k: getattr(input_data, k)
        for k in config._fingerprint_fields
        if hasattr(input_data, k)
    }
    raw_str = json.dumps(fingerprint_dict, sort_keys=True)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:24]


async def _write_trail_async(output_dir: Path, trail: list) -> None:
    """辅助函数：将审计轨迹落盘"""
    trail_path = output_dir / "decision_trail.json"
    with open(trail_path, "w") as f:
        json.dump(trail, f, indent=2)


async def audit_repository_workflow(
    config: AuditConfig,
    input_data: AuditInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """
    业务事务: 下载代码 -> 审计 -> 返回结果。
    严格的三件套签名。
    """
    from obase.cost_tracker import CostTracker, current_cost

    decision_trail = []

    # 1. 成本上下文初始化 (规范 §5.6 C1)
    cost_token = None
    if "cost" in config._enabled_pillars:
        cost_token = current_cost.set(CostTracker())

    status = "completed"
    error = None
    findings = {}

    try:
        if on_step:
            on_step({"step": 1, "msg": f"Starting audit for {input_data.target_file}"})
        decision_trail.append({"action": "start", "file": input_data.target_file})

        # 【伪代码】: 从 obase 的 ProviderRegistry 获取资源实例
        # vfs = ProviderRegistry.get_vfs()
        # llm = ProviderRegistry.get_llm(config.llm_provider)

        # 2. 组合 oprim: 物理执行环境准备 (克隆代码)
        # await execute_sandbox_cmd(f"git clone {input_data.repo_url} workspace", vfs=vfs)
        decision_trail.append({"action": "git_clone", "status": "success"})

        # 3. 组合 oskill: 执行审计技能
        # findings = await audit_sandbox_script(
        #     target_path=f"workspace/{input_data.target_file}",
        #     llm=llm,
        #     vfs=vfs
        # )
        decision_trail.append({"action": "audit_script", "findings": findings})

        if on_step:
            on_step({"step": 2, "msg": "Audit completed."})

    except asyncio.CancelledError:
        # 规范 §5.6 C4: 取消任务必须重新抛出，不可吞没
        status = "cancelled"
        error = {"reason": "Task was cancelled or timed out"}
        decision_trail.append({"action": "cancelled"})
        raise

    except Exception as e:
        # 规范 §5.4: 业务失败绝对不 raise，优雅返回
        status = "failed"
        error = {"type": type(e).__name__, "message": str(e)}
        decision_trail.append({"action": "error", "error": error})

    finally:
        # 4. 强制审计落盘 (规范 §5.6 C4)
        if "decision_trail" in config._enabled_pillars:
            output_dir.mkdir(parents=True, exist_ok=True)
            # 使用 asyncio.shield 保护写操作不被取消指令打断
            await asyncio.shield(_write_trail_async(output_dir, decision_trail))

        # 成本结算必须在 reset 之前读取
        cost_usd = 0.0
        if cost_token is not None:
            cost_usd = current_cost.get().total_usd
            current_cost.reset(cost_token)

    # 5. 必须返回标准 Dict 结构
    # 注意: return 刻意置于 finally 之外 — 若 return 写在 finally 内,
    # Python 的 finally-return 语义会吞掉 except CancelledError 重新抛出的
    # 异常, 违背规范 §5.6 C4"取消必须重新 raise"。置于 finally 之后:
    # 正常/失败路径都会执行到本行, 取消路径的 raise 则跳过本行继续传播。
    return {
        "status": status,
        "error": error,
        "findings": findings,
        "fingerprint": compute_fingerprint_for(config, input_data)
        if "fingerprint" in config._enabled_pillars
        else None,
        "cost_usd": cost_usd,
    }
