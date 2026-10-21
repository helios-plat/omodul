"""3O 范式全栈 — omodul 业务事务: 证据精炼 (元素 23).

去重 -> 置信度排序 -> 截断: 将原始证据收敛为结构化发现。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from obase import OmodulBaseConfig
from pydantic import BaseModel


class EvidenceConfig(OmodulBaseConfig):
    _omodul_name = "evidence_refine"
    _omodul_version = "1.0.0"
    _enabled_pillars = {"decision_trail", "fingerprint"}
    _fingerprint_fields = {"anon_session_ref", "source_hint"}

    max_findings: int = 10  # 业务实例参数


class EvidenceInput(BaseModel):
    anon_session_ref: str
    source_hint: str = ""
    raw_evidence: list[dict[str, Any]]


def compute_fingerprint_for(config: EvidenceConfig, input_data: EvidenceInput) -> str:
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


async def evidence_refine_workflow(
    config: EvidenceConfig,
    input_data: EvidenceInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """业务事务: 去重 -> 置信度排序 -> 截断 (纯内存精炼, 不落业务数据)。"""
    decision_trail = []
    status = "completed"
    error = None
    findings = {}

    try:
        if on_step:
            on_step({"step": 1, "msg": "dedupe evidence"})

        # 1. 去重 (按内容语义: 同 content 视为重复证据, 保留首见副本)
        seen: set[str] = set()
        refined: list[dict[str, Any]] = []
        for item in input_data.raw_evidence:
            dedupe_key = item.get("content", item)
            content = (
                dedupe_key
                if isinstance(dedupe_key, str)
                else json.dumps(dedupe_key, sort_keys=True, ensure_ascii=False)
            )
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
            if digest in seen:
                continue
            seen.add(digest)
            refined.append(item)

        # 2. 置信度降序
        refined.sort(key=lambda x: float(x.get("confidence", 0.0)), reverse=True)

        # 3. 截断
        findings = {
            "evidence": refined[: config.max_findings],
            "total": len(refined),
        }
        decision_trail.append({"action": "refine", "kept": len(findings["evidence"])})

        if on_step:
            on_step({"step": 2, "msg": "refine done"})

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
