"""
试卷分析全流程业务事务
====================
omodul/analyze_paper.py
"""

from __future__ import annotations
import uuid
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, List, Dict, Any, Optional, Callable
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

from omodul.base import BaseConfig, build_fingerprint, standard_return
from oprim.llm_oprims import ocr_paper, PaperOCRResult
from oskill.paper_grading import process_single_question
from omodul.cognitive import process_interaction_workflow, InteractionConfig, InteractionInput
from obase.cognitive_store import BaseCognitiveStore
from services.models import Paper, PaperStatus

class AnalyzePaperConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "analyze_paper_workflow"
    _omodul_version: ClassVar[str] = "0.1.0"
    _fingerprint_fields: ClassVar[set[str]] = {"subject", "grade"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report", "cost"}

    subject: str = "math"
    grade: str = "高三"

class AnalyzePaperInput(BaseModel):
    paper_id: uuid.UUID
    student_id: uuid.UUID
    image_b64_list: List[str]

class AnalyzePaperFindings(BaseModel):
    paper_id: str
    total_questions: int
    correct_count: int
    wrong_count: int
    wrong_questions: List[dict]
    cognitive_updates: List[dict]

async def analyze_paper_workflow(
    config: AnalyzePaperConfig,
    input_data: AnalyzePaperInput,
    store: BaseCognitiveStore,
    session: AsyncSession,
    output_dir: Optional[Path] = None,
    *,
    on_step: Optional[Callable[[dict], None]] = None,
) -> dict:
    """
    试卷分析全流程业务事务。
    
    Stage pipeline:
    1. OCR: 提取题目
    2. Grade & Profile: 批改并分析错题 (oskill.paper_grading)
    3. Cognitive Update: 错题触发内核更新 (omodul.cognitive)
    4. Update Paper Status: 更新试卷状态为 done
    """
    
    trail = []
    
    # 1. OCR
    if on_step: on_step({"step": "ocr", "status": "started"})
    
    # 目前简化：处理第一张图
    if not input_data.image_b64_list:
        return standard_return(findings=None, status="failed", error="No images provided")
        
    ocr_res: PaperOCRResult = await ocr_paper(
        image_b64=input_data.image_b64_list[0], 
        subject=config.subject
    )
    trail.append({"step": "ocr", "question_count": len(ocr_res.questions)})
    
    # 2 & 3. Grade, Profile & Cognitive Update
    wrong_questions = []
    cognitive_updates = []
    correct_count = 0
    
    for q in ocr_res.questions:
        # 批改与错题分析
        res = await process_single_question(
            session=session,
            student_id=input_data.student_id,
            paper_id=input_data.paper_id,
            question_text=q.get("question_text", ""),
            student_answer=q.get("student_answer", ""),
            correct_answer=q.get("correct_answer", ""),
            subject=config.subject
        )
        
        if res["status"] == "correct":
            correct_count += 1
            # TODO: 按照规格，目前只处理错题的认知更新。
            # 如果以后需要处理正确题目的认知更新，需要先识别其 KC。
        else:
            wrong_questions.append(res)
            
            # 为错题中的每个知识点触发认知更新 (Task 3.4)
            for kc_id in res.get("knowledge_points", []):
                interaction_input = InteractionInput(
                    student_id=input_data.student_id,
                    kc_id=kc_id,
                    is_correct=False,
                    question_id=uuid.UUID(res["wq_id"]),
                    source="paper"
                )
                # 调用 cognitive omodul (H1-modul 允许在服务层或大编排中组合，但 CLAUDE.md 说"不调 sibling omodul"?)
                # Wait, CLAUDE.md says "不让 omodul 调 omodul (H1-modul 严格，含'包装模式')".
                # "多 omodul 协作在服务层".
                
                # Oops, so analyze_paper_workflow should probably NOT call process_interaction_workflow directly?
                # But then where should it be?
                # Maybe I should just call the oskill.cognitive_update and do the store operations here?
                # Or maybe analyze_paper_workflow is NOT an omodul but a Service?
                
                # Let's check CLAUDE.md again.
                # "omodul: ≥2 oskill/oprim 组合的业务事务. 不调 sibling omodul".
                # "多 omodul 协作在服务层".
                
                # So analyze_paper_workflow being an omodul should not call process_interaction_workflow.
                # Instead, it should return the results, and the Service should call process_interaction_workflow.
                
                # But Task 3.4 DoD says: "上传后 /v1/mastery 反映变化".
                # This means the /v1/papers/upload (or a follow-up) must trigger it.
                
                # I'll implement the cognitive update logic directly using oskill inside analyze_paper_workflow
                # if I want to keep it as an omodul, OR I make it call oskill.cognitive_update.
                
                from oskill.cognitive_state import cognitive_update, CognitiveUpdateInput
                
                # 获取当前状态
                state, card_dict = await store.get_or_create(input_data.student_id, kc_id)
                
                # 算法更新
                upd_input = CognitiveUpdateInput(
                    state=state,
                    card_dict=card_dict,
                    is_correct=False,
                    now=datetime.now(timezone.utc)
                )
                upd_res = cognitive_update(input=upd_input)
                
                # 保存
                await store.save(input_data.student_id, kc_id, upd_res.state, upd_res.card_dict)
                
                # 追加事件
                await store.append_event(input_data.student_id, kc_id, {
                    "question_id": uuid.UUID(res["wq_id"]),
                    "source": "paper",
                    "is_correct": False,
                    "fsrs_rating": upd_res.rating_val,
                    "occurred_at": datetime.now(timezone.utc)
                })
                
                cognitive_updates.append({
                    "kc_id": kc_id,
                    "p_mastery": upd_res.state.current()
                })

    # 4. 更新试卷状态
    stmt = update(Paper).where(Paper.id == input_data.paper_id).values(
        status=PaperStatus.done,
        ocr_result=ocr_res.model_dump()
    )
    await session.execute(stmt)
    
    findings = AnalyzePaperFindings(
        paper_id=str(input_data.paper_id),
        total_questions=len(ocr_res.questions),
        correct_count=correct_count,
        wrong_count=len(wrong_questions),
        wrong_questions=wrong_questions,
        cognitive_updates=cognitive_updates
    )
    
    trail.append({"step": "grading_done", "wrong_count": len(wrong_questions)})
    
    return standard_return(
        findings=findings,
        status="completed",
        trail=trail if "decision_trail" in config._enabled_pillars else None
    )

__version__ = "0.1.0"
__manifest__ = {
    "version": __version__,
    "updated_at": "2026-06-13",
    "elements": [
        {"name": "analyze_paper_workflow", "layer": "omodul", "summary": "试卷分析全流程（OCR+批改+分析+内核更新）"},
    ]
}
