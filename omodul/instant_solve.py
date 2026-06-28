"""即时问答 omodul (omodul_instant_solve)

职责：随手拍/即时问答统一入口，通过 cold_start_single 启动苏格拉底引导。
"""

from __future__ import annotations
import uuid
import asyncio
from typing import ClassVar, List, Dict, Any, Optional
from pydantic import BaseModel
from omodul.base import BaseConfig, build_fingerprint, standard_return
from oskill.cold_start_single import cold_start_single, ColdStartInput
from obase.interaction_history import start_interaction_session, update_interaction_session

class InstantSolveConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "instant_solve"
    _omodul_version: ClassVar[str] = "0.1.0"
    _enabled_pillars: ClassVar[set] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[set[str]] = {"student_id"}

class InstantSolveInput(BaseModel):
    student_id: uuid.UUID
    input_type: str # 'image', 'text', 'voice'
    content: str    # b64, url, or raw text

async def instant_solve(
    config: InstantSolveConfig,
    input_data: InstantSolveInput,
    *,
    caller: Any,
    pool: Any # PgPool
) -> dict:
    """即时问答全流程。"""
    
    trail = []
    start_ts = asyncio.get_event_loop().time()
    
    # 1. 计算指纹 (Fingerprint)
    input_hash = hashlib.sha256(input_data.content.encode()).hexdigest()
    fp = build_fingerprint(config, input_hash)
    trail.append({"event": "fingerprint_computed", "fp": fp})

    # 2. 启动持久化会话 (History & Trail)
    session_id = await start_interaction_session(
        pool=pool,
        student_id=input_data.student_id,
        input_type=input_data.input_type,
        initial_input=input_data.content[:100] # 简略
    )
    trail.append({"event": "session_started", "session_id": str(session_id)})

    # 3. 执行冷启动 (Orchestration)
    try:
        cs_res = await cold_start_single(
            ColdStartInput(
                student_id=str(input_data.student_id),
                input_type=input_data.input_type,
                content=input_data.content
            ),
            caller=caller
        )
        trail.append({"event": "cold_start_completed", "status": cs_res.get("status")})
        
        # 4. 更新会话状态 (Report & Decision Trail)
        if cs_res.get("status") == "ready_for_guidance":
            await update_interaction_session(
                pool=pool,
                session_id=session_id,
                metacog_eval=cs_res.get("metacog"),
                decision_trail=trail
            )
        
        # 5. 成本计算 (Cost) - 模拟
        cost_usd = 0.005 # 假定
        
        return standard_return(
            findings=cs_res,
            status="success",
            fingerprint=fp,
            trail=trail,
            cost_usd=cost_usd
        )
        
    except Exception as e:
        trail.append({"event": "error", "message": str(e)})
        return standard_return(
            findings=None,
            status="failed",
            error=str(e),
            trail=trail,
            cost_usd=0.0
        )

import hashlib
__version__ = "0.1.0"
