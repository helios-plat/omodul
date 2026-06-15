"""
试卷处理业务事务
================
omodul/paper.py
"""

from __future__ import annotations
import uuid
from pathlib import Path
from typing import Optional, Any
from datetime import datetime, timezone
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert

from omodul.base import BaseConfig, standard_return
from obase.oss import upload_file
from services.models import Paper, PaperStatus

class PaperConfig(BaseConfig):
    _omodul_name = "paper_workflow"
    _omodul_version = "0.1.0"
    _enabled_pillars = {"decision_trail"}

class PaperUploadInput(BaseModel):
    student_id: uuid.UUID
    local_file_path: Path
    filename: str
    subject: str = "math"

async def upload_paper_workflow(
    config: PaperConfig,
    input_data: PaperUploadInput,
    session: AsyncSession
) -> dict:
    """上传一张试卷并记录到数据库（processing 状态）。"""
    
    # 1. 生成存储路径
    paper_id = uuid.uuid4()
    ext = Path(input_data.filename).suffix or ".jpg"
    object_name = f"papers/{input_data.student_id}/{paper_id}{ext}"
    
    # 2. 上传 OSS
    try:
        await upload_file(
            input_data.local_file_path, 
            object_name, 
            content_type="image/jpeg" # 假设是图片
        )
    except Exception as e:
        return standard_return(
            findings=None,
            status="failed",
            error=f"OSS upload failed: {str(e)}"
        )
    
    # 3. 记库 (注意：字段名必须与 services/models.py 中的 Paper 类匹配)
    # models.py 中 Paper 的字段是：image_urls (JSONB), ocr_result (JSONB), status, storage_tier
    # 没有 oss_path, raw_ocr_json, updated_at
    ins_stmt = insert(Paper).values(
        id=paper_id,
        student_id=input_data.student_id,
        subject=input_data.subject,
        image_urls={"original": object_name}, # 用 image_urls 存储路径
        status=PaperStatus.processing,
        created_at=datetime.now(timezone.utc)
    )
    await session.execute(ins_stmt)
    await session.commit()
    
    trail = [
        {"step": "generate_id", "paper_id": str(paper_id)},
        {"step": "oss_upload", "object_name": object_name},
        {"step": "db_record", "status": "processing"}
    ]
    
    return standard_return(
        findings={"paper_id": str(paper_id), "status": "processing"},
        status="completed",
        trail=trail if "decision_trail" in config._enabled_pillars else None
    )
