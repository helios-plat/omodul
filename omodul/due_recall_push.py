"""到期召回推送 omodul (omodul_due_recall_push)

职责：监听 FSRS 到期项并触发主动召回推送，引导至变式复习。
"""

from __future__ import annotations
import uuid
from typing import ClassVar, List, Dict, Any, Optional
from pydantic import BaseModel
from omodul.base import BaseConfig, build_fingerprint, standard_return
from obase.notify.telegram import telegram_send as send_message # 假设使用现有的通知通道

class DueRecallPushConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "due_recall_push"
    _omodul_version: ClassVar[str] = "0.1.0"
    _fingerprint_fields: ClassVar[set[str]] = {"batch_id"}
    
    push_frequency_limit: int = 1 # 每天最多推送次数

class DueRecallPushInput(BaseModel):
    batch_id: str
    due_items: List[dict] # 包含 student_id, kc_id, question_text 等

async def due_recall_push_workflow(
    config: DueRecallPushConfig,
    input_data: DueRecallPushInput,
    *,
    pool: Any
) -> dict:
    """处理召回推送。"""
    
    trail = []
    fp = build_fingerprint(config, input_data.batch_id)
    trail.append({"event": "fingerprint_computed", "fp": fp})

    push_count = 0
    errors = []
    
    for item in input_data.due_items:
        student_id = item.get("student_id")
        kc_id = item.get("kc_id")
        
        # 1. 构造推送文案 (Decision Trail)
        message = f"你的知识点【{kc_id}】该复习啦！点击开始同类题挑战。"
        
        try:
            # 2. 调用现有预警系统/通知通道 (Channels)
            # 这里简化为直接调用 obase.notify
            await send_message(chat_id=str(student_id), text=message)
            push_count += 1
            trail.append({"event": "pushed", "student_id": str(student_id), "kc_id": kc_id})
        except Exception as e:
            errors.append(str(e))
            trail.append({"event": "push_failed", "student_id": str(student_id), "error": str(e)})

    # 3. Report
    findings = {
        "total_items": len(input_data.due_items),
        "success_count": push_count,
        "error_count": len(errors)
    }
    
    # 4. Cost - 模拟推送成本
    cost_usd = push_count * 0.001
    
    status = "success" if push_count > 0 else "failed"
    if not input_data.due_items: status = "skipped"

    return standard_return(
        findings=findings,
        status=status,
        error="; ".join(errors) if errors else None,
        fingerprint=fp,
        trail=trail,
        cost_usd=cost_usd
    )

__version__ = "0.1.0"
