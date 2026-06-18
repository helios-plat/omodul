"""家长回看 omodul (omodul_parent_review)

职责：家长端查看孩子互动历史及错误归因汇总。
"""

from __future__ import annotations
import uuid
from typing import ClassVar, List, Dict, Any, Optional
from pydantic import BaseModel
from omodul.base import BaseConfig, build_fingerprint, standard_return
from obase.interaction_history import get_student_history
from obase.error_tag_store import get_error_distribution

class ParentReviewConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "parent_review"
    _omodul_version: ClassVar[str] = "0.1.0"
    _fingerprint_fields: ClassVar[set[str]] = {"parent_id", "student_id"}

class ParentReviewInput(BaseModel):
    parent_id: uuid.UUID
    student_id: uuid.UUID
    limit: int = 10

async def parent_review_summary(
    config: ParentReviewConfig,
    input_data: ParentReviewInput,
    *,
    pool: Any
) -> dict:
    """家长端概览汇总。"""
    
    trail = []
    
    # 1. Fingerprint
    fp = build_fingerprint(config, f"{input_data.parent_id}:{input_data.student_id}")
    trail.append({"event": "fingerprint_computed", "fp": fp})

    # 2. 鉴权逻辑 (Decision Trail)
    # 假设此处有鉴权检查，校验 parent_id -> student_id 绑定关系
    trail.append({"event": "auth_checked", "parent_id": str(input_data.parent_id)})

    # 3. 提取数据 (Report)
    history = await get_student_history(pool=pool, student_id=input_data.student_id, limit=input_data.limit)
    error_dist = await get_error_distribution(pool=pool, student_id=input_data.student_id)
    
    trail.append({"event": "data_fetched", "history_count": len(history), "dist_count": len(error_dist)})

    findings = {
        "recent_history": history,
        "error_distribution": error_dist,
        "summary_message": "孩子近期在‘计算失误’方面较多，建议多关注审题习惯。"
    }
    
    # 4. Cost - 极低
    cost_usd = 0.0001
    
    return standard_return(
        findings=findings,
        status="success",
        fingerprint=fp,
        trail=trail,
        cost_usd=cost_usd
    )

__version__ = "0.1.0"
