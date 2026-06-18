"""
omodul.book_understanding_synthesize — Book-level deep-understanding synthesis.

Pillars: decision_trail, report
Fingerprint fields: book_substrate_id, doc_type

Mandates (CI-checked):
  - doc_type="science" → claim_grade can reach "high"
  - doc_type="literature" → grade upper limit "low"
  - stance_marker must be present and non-empty in every claim
  - argument evidence grades are independent per evidence item
  - is_synthesis=True + synthesis_note hardcoded
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

from obase.provider_registry import ProviderRegistry

from omodul._base import (
    BaseConfig, CostTracker, Trail, build_result, compute_fingerprint,
    write_report,
)


# ---------------------------------------------------------------------------
# Grade helpers
# ---------------------------------------------------------------------------

_GRADE_RANKS: dict[str, int] = {
    "unverified": 0, "low": 1, "medium": 2, "high": 3, "verified": 4,
}

_DOC_TYPE_GRADE_CAP: dict[str, str] = {
    "science": "high",
    "economics": "medium",
    "psychology": "medium",
    "history": "medium",
    "literature": "low",
}


def _cap_grade(grade: str, cap: str) -> str:
    cap_rank = _GRADE_RANKS.get(cap, 1)
    grade_rank = _GRADE_RANKS.get(grade, 0)
    return cap if grade_rank > cap_rank else grade


# ---------------------------------------------------------------------------
# Config / Findings
# ---------------------------------------------------------------------------

class BookUnderstandingConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "book_understanding_synthesize"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set] = {"decision_trail", "report"}
    _fingerprint_fields: ClassVar[set] = {"book_substrate_id", "doc_type"}

    book_substrate_id: str
    doc_type: str = "science"


class BookUnderstandingFindings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    book_ku_id: str
    summary: str
    is_synthesis: bool = True
    synthesis_note: str = "AII综合，非原文断言"
    main_claims: list[dict]         # [{claim, stance_marker, claim_grade}]
    argument_structure: list[dict]  # [{point, evidence:[{text, grade}]}]
    key_concept_ku_ids: list[str]
    structure: str
    doc_type: str

    @field_validator("is_synthesis", mode="before")
    @classmethod
    def _force_is_synthesis(cls, v: Any) -> bool:
        return True

    @field_validator("synthesis_note", mode="before")
    @classmethod
    def _force_synthesis_note(cls, v: Any) -> str:
        return "AII综合，非原文断言"


# ---------------------------------------------------------------------------
# Fingerprint helper
# ---------------------------------------------------------------------------

def compute_fingerprint_for_book_understanding_synthesize(
    book_substrate_id: str, doc_type: str
) -> str:
    return compute_fingerprint({"book_substrate_id": book_substrate_id, "doc_type": doc_type})


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

async def book_understanding_synthesize(
    config: BookUnderstandingConfig,
    input_data: Any,   # BookUnderstandingInput (oprim._aii_graph_types)
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Produce a structured book-level understanding synthesis.

    Returns build_result dict with decision_trail, report_path, and findings.
    """
    trail = Trail()
    cost = CostTracker()
    fingerprint = compute_fingerprint_for_book_understanding_synthesize(
        config.book_substrate_id, config.doc_type
    )

    ku_ids = list(getattr(input_data, "ku_ids", []))
    if not ku_ids:
        return build_result(
            status="failed",
            error={"type": "ValueError", "message": "ku_ids is empty"},
            fingerprint=fingerprint,
            trail=trail,
            trail_path=None,
            cost_usd=0.0,
        )

    try:
        llm = ProviderRegistry.get().llm(config.llm_provider)

        ku_texts: list[str] = list(getattr(input_data, "ku_texts", []))
        ku_grades: list[str] = list(getattr(input_data, "ku_grades", []))

        grade_cap = _DOC_TYPE_GRADE_CAP.get(config.doc_type, "medium")

        trail.record(
            event="start",
            book_substrate_id=config.book_substrate_id,
            doc_type=config.doc_type,
            grade_cap=grade_cap,
            n_kus=len(ku_ids),
            fingerprint=fingerprint,
        )
        _notify(on_step, "analyze", "started")

        ku_block = "\n\n".join(
            f"[{i + 1}] {ku_ids[i]}: {ku_texts[i] if i < len(ku_texts) else ''}"
            for i in range(len(ku_ids))
        )

        doc_type_hint = {
            "science": "科学/技术文献：可使用较高置信度，但区分实验事实与理论推断",
            "economics": "经济学文献：区分实证研究与理论模型，grade上限medium",
            "psychology": "心理学文献：区分元分析与单一研究，grade上限medium",
            "history": "历史文献：区分一手史料与二手叙述，grade上限medium",
            "literature": "文学文献：所有解读均为诠释，grade上限low",
        }.get(config.doc_type, "文献")

        prompt = f"""\
你是一位学术分析专家，请对以下书籍知识单元进行深度理解综合分析。

文献类型提示：{doc_type_hint}
文献标识：{config.book_substrate_id}

知识单元：
{ku_block}

请输出以下 JSON 格式（严格 JSON，无 markdown）：
{{
  "summary": "全书摘要（200-400字）",
  "main_claims": [
    {{"claim": "核心主张", "stance_marker": "《作品》主张/作者认为/研究表明", "claim_grade": "low|medium|high"}}
  ],
  "argument_structure": [
    {{"point": "论点", "evidence": [{{"text": "论据", "grade": "low|medium|high"}}]}}
  ],
  "key_concept_ku_ids": ["相关ku_id列表"],
  "structure": "全书结构描述"
}}

注意：stance_marker 必须存在且非空，用于标记主张来源（如"《X》主张"），不是"X是真理"。"""

        resp = await llm(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
        )
        text = _extract_text(resp)
        cost.add_from_response(resp, model=config.llm_model)

        trail.record(event="llm_done", text_len=len(text))
        _notify(on_step, "analyze", "done")

        parsed = _parse_json(text)

        # Post-process: enforce grade caps per doc_type (core logic, not just docstring)
        main_claims = parsed.get("main_claims", [])
        for claim in main_claims:
            raw_grade = claim.get("claim_grade", "low")
            claim["claim_grade"] = _cap_grade(raw_grade, grade_cap)
            # Ensure stance_marker is present and non-empty
            if not claim.get("stance_marker", "").strip():
                claim["stance_marker"] = f"（{config.book_substrate_id} 文本主张）"

        argument_structure = parsed.get("argument_structure", [])
        for arg in argument_structure:
            for ev in arg.get("evidence", []):
                raw_grade = ev.get("grade", "low")
                ev["grade"] = _cap_grade(raw_grade, grade_cap)

        _notify(on_step, "report", "started")

        book_ku_id = f"book_{fingerprint[:8]}_{uuid.uuid4().hex[:6]}"

        findings = BookUnderstandingFindings(
            book_ku_id=book_ku_id,
            summary=parsed.get("summary", ""),
            main_claims=main_claims,
            argument_structure=argument_structure,
            key_concept_ku_ids=parsed.get("key_concept_ku_ids", []),
            structure=parsed.get("structure", ""),
            doc_type=config.doc_type,
        )

        # Write report
        report_content = _build_report(findings, config)
        report_path = write_report(
            report_content,
            output_dir=output_dir,
            name=f"book_understanding_{fingerprint[:8]}",
            fmt="markdown",
        )

        trail.record(event="report_done", report_path=str(report_path))
        _notify(on_step, "report", "done")

        trail_path = trail.write(output_dir)

        return build_result(
            status="completed",
            error=None,
            fingerprint=fingerprint,
            trail=trail,
            trail_path=trail_path,
            report_path=str(report_path),
            cost_usd=cost.total_usd,
            **findings.model_dump(),
        )

    except asyncio.CancelledError:
        trail.record(event="cancelled")
        trail.write(output_dir)
        raise

    except Exception as exc:
        trail.record(event="error", error_type=type(exc).__name__, message=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fingerprint,
            trail=trail,
            trail_path=None,
            cost_usd=cost.total_usd,
        )


def _extract_text(resp: dict) -> str:
    for block in resp.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            return block["text"].strip()
    return ""


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        val = json.loads(text)
        if isinstance(val, dict):
            return val
    except json.JSONDecodeError:
        pass
    return {
        "summary": text[:500] if text else "",
        "main_claims": [],
        "argument_structure": [],
        "key_concept_ku_ids": [],
        "structure": "",
    }


def _build_report(findings: BookUnderstandingFindings, config: BookUnderstandingConfig) -> str:
    claims_md = "\n".join(
        f"- [{c.get('claim_grade','?')}] {c.get('stance_marker','')} {c.get('claim','')}"
        for c in findings.main_claims
    )
    args_md = "\n".join(
        f"- {a.get('point','')} "
        + ", ".join(f"[{e.get('grade','?')}]{e.get('text','')}" for e in a.get("evidence", []))
        for a in findings.argument_structure
    )
    return (
        f"# Book Understanding: {config.book_substrate_id}\n\n"
        f"**doc_type**: {config.doc_type}  \n"
        f"**synthesis_note**: {findings.synthesis_note}\n\n"
        f"## Summary\n\n{findings.summary}\n\n"
        f"## Main Claims\n\n{claims_md or '(none)'}\n\n"
        f"## Argument Structure\n\n{args_md or '(none)'}\n\n"
        f"## Structure\n\n{findings.structure}\n"
    )


def _notify(on_step: Any, step: str, state: str) -> None:
    if on_step is not None:
        try:
            on_step(step=step, state=state)
        except Exception:
            pass
