"""错题诊断 omodul (omodul_error_journal)

职责：错题本主动入口，提供错误归因诊断（粗心 vs 概念不清）。
"""

from __future__ import annotations
import uuid
from typing import ClassVar, List, Dict, Any, Optional
from pydantic import BaseModel
from omodul.base import BaseConfig, build_fingerprint, standard_return
from obase.error_tag_store import get_error_distribution

class ErrorJournalConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "error_journal"
    _omodul_version: ClassVar[str] = "0.1.0"
    _enabled_pillars: ClassVar[set] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[set[str]] = {"student_id"}

class ErrorJournalInput(BaseModel):
    student_id: uuid.UUID
    kc_id: Optional[str] = None

async def error_journal_diagnostic(
    config: ErrorJournalConfig,
    input_data: ErrorJournalInput,
    *,
    pool: Any # PgPool
) -> dict:
    """错题诊断。"""
    
    trail = []
    
    # 1. Fingerprint
    fp = build_fingerprint(config, str(input_data.student_id))
    trail.append({"event": "fingerprint_computed", "fp": fp})

    # 2. 从 obase 提取数据 (Decision Trail)
    dist = await get_error_distribution(
        pool=pool, 
        student_id=input_data.student_id,
        kc_id=input_data.kc_id
    )
    trail.append({"event": "data_extracted", "count": len(dist)})

    # 3. 诊断逻辑 (Report)
    # 简单的归因汇总：找出占比最高的错误类型
    primary_reason = "未知"
    if dist:
        sorted_dist = sorted(dist, key=lambda x: x["count"], reverse=True)
        primary_reason = sorted_dist[0]["primary_tag"]
    
    findings = {
        "distribution": dist,
        "primary_diagnostic": f"学生主要错误原因为: {primary_reason}",
        "suggestion": "建议针对该类型错误进行专项练习。"
    }
    
    # 4. Cost - 纯数据库查询，成本极低
    cost_usd = 0.0001
    
    return standard_return(
        findings=findings,
        status="success",
        fingerprint=fp,
        trail=trail,
        cost_usd=cost_usd
    )

__version__ = "0.1.0"
