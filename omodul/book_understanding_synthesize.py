"""
omodul.book_understanding_synthesize — Book-level deep-understanding synthesis.

Pillars: decision_trail, report
Fingerprint fields: book_substrate_id, doc_type

Mandates (CI-checked):
  - source_credibility="textbook" → claim_grade can reach "high"
  - source_credibility="bestseller" → claim_grade upper limit "low"
  - doc_type="literature" → grade upper limit "low"
  - stance_marker must be present and non-empty in every claim
  - argument evidence must be independent (not a restatement of point)
  - structure must be a list of dicts [{title, summary, children}]
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

# doc_type caps (fallback when source_credibility not determined)
_DOC_TYPE_GRADE_CAP: dict[str, str] = {
    "science": "high",
    "economics": "medium",
    "psychology": "medium",
    "history": "medium",
    "literature": "low",
}

# source_credibility caps (takes precedence over doc_type, stricter wins)
_SOURCE_CRED_GRADE_CAP: dict[str, str] = {
    "textbook": "high",
    "academic_monograph": "medium",
    "popular_science": "medium",
    "bestseller": "low",
    "controversial": "unverified",
}


def _cap_grade(grade: str, cap: str) -> str:
    cap_rank = _GRADE_RANKS.get(cap, 1)
    grade_rank = _GRADE_RANKS.get(grade, 0)
    return cap if grade_rank > cap_rank else grade


def _effective_grade_cap(doc_type: str, book_type: str) -> str:
    """Take the stricter of doc_type cap and source_credibility cap."""
    dt_cap = _DOC_TYPE_GRADE_CAP.get(doc_type, "medium")
    sc_cap = _SOURCE_CRED_GRADE_CAP.get(book_type, "medium")
    dt_rank = _GRADE_RANKS.get(dt_cap, 2)
    sc_rank = _GRADE_RANKS.get(sc_cap, 2)
    return dt_cap if dt_rank <= sc_rank else sc_cap


def _normalize_structure(raw: Any) -> list[dict]:
    """Ensure structure is always [{title, summary, children}] regardless of LLM output."""
    if isinstance(raw, list):
        result = []
        for item in raw:
            if isinstance(item, dict):
                result.append({
                    "title": str(item.get("title", "")),
                    "summary": str(item.get("summary", item.get("description", ""))),
                    "children": _normalize_structure(item.get("children", [])),
                })
            elif isinstance(item, str):
                result.append({"title": item, "summary": "", "children": []})
        return result
    if isinstance(raw, str) and raw.strip():
        # LLM ignored instructions and returned a string — wrap it
        return [{"title": "全书结构", "summary": raw.strip(), "children": []}]
    return []


# ---------------------------------------------------------------------------
# Config / Findings
# ---------------------------------------------------------------------------

class BookUnderstandingConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "book_understanding_synthesize"
    _omodul_version: ClassVar[str] = "2.0.0"
    _enabled_pillars: ClassVar[set] = {"decision_trail", "report"}
    _fingerprint_fields: ClassVar[set] = {"book_substrate_id", "doc_type"}

    book_substrate_id: str
    doc_type: str = "science"
    book_title: str = ""  # 书名，供LLM判断book_type；空则依赖内容推断


class BookUnderstandingFindings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    book_ku_id: str
    summary: str
    is_synthesis: bool = True
    synthesis_note: str = "AII综合，非原文断言"
    # ── 来源可信度 (★新) ──
    source_credibility: dict       # {book_type, credibility_level, credibility_note}
    # ── 组织性深度字段 (★新) ──
    problem_statement: str
    overview_oneline: str
    learning_thread: str
    structure: list[dict]          # [{title, summary, children}] (★改:str→list)
    knowledge_categories: dict     # {theoretical, empirical, normative, methodological}
    applicability: str
    core_takeaways: list[str]
    # ── 原有字段 (★改良) ──
    main_claims: list[dict]        # [{claim, stance, stance_marker, claim_grade}]
    argument_structure: list[dict] # [{point, evidence:[{text,grade}], boundary}]
    key_concept_ku_ids: list[str]
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

        doc_type_fallback_cap = _DOC_TYPE_GRADE_CAP.get(config.doc_type, "medium")

        trail.record(
            event="start",
            book_substrate_id=config.book_substrate_id,
            doc_type=config.doc_type,
            doc_type_fallback_cap=doc_type_fallback_cap,
            n_kus=len(ku_ids),
            fingerprint=fingerprint,
        )
        _notify(on_step, "analyze", "started")

        # ★ 把 grade 喂给 LLM，让它参考真实可信度
        ku_block = "\n\n".join(
            f"[{i + 1}] grade={ku_grades[i] if i < len(ku_grades) else 'unverified'} | "
            f"{ku_ids[i]}: {ku_texts[i] if i < len(ku_texts) else ''}"
            for i in range(len(ku_ids))
        )

        doc_type_hint = {
            "science": "科学/技术文献",
            "economics": "经济学文献（区分实证研究与理论模型，grade上限medium）",
            "psychology": "心理学文献（区分元分析与单一研究，grade上限medium）",
            "history": "历史文献（区分一手史料与二手叙述，grade上限medium）",
            "literature": "文学文献（所有解读均为诠释，grade上限low）",
        }.get(config.doc_type, "文献")

        # 书名行：有书名则明确展示，无则用 substrate_id
        book_title_line = f"书名：{config.book_title}" if config.book_title else f"文献标识：{config.book_substrate_id}"

        prompt = f"""\
你是一位学术分析专家。基于以下书籍知识单元（KU），对该书做组织性深度理解。
目标：把书讲透、讲诚实——忠于原文，不推断，不发挥。

{book_title_line}
文献类型参考：{doc_type_hint}

书类型判断指南（据书名+内容）：
- textbook：大学教材，系统性强，作者为学者，出版社为学术出版社（如 Cengage、Pearson、MIT Press）
- academic_monograph：学术专著，同行评议，聚焦单一主题，面向研究者
- popular_science：面向大众的科普读物，简化专业知识，强调可读性
- bestseller：商业类畅销书、管理方法论、自我提升、投资心理学等，作者多为顾问/记者/博主，观点个人化
- controversial：争议性、边缘领域、强烈意识形态立场的书籍
★判断保守：拿不准的偏低（popular_science/bestseller 优于错标 textbook）；DK出版社大图鉴/Big Ideas系列=popular_science；商业方法论/投资心理学=bestseller

知识单元（每条含可信度grade供参考）：
{ku_block}

输出严格JSON，无markdown。

{{
  "source_credibility": {{
    "book_type": "textbook | academic_monograph | popular_science | bestseller | controversial",
    "credibility_level": "high | moderate | low | unverified",
    "credibility_note": "判断依据一句话（必须说明为何选此类型；保守：不确定偏低）"
  }},
  "problem_statement": "这本书在解决什么问题/核心困境（忠于原文，50-100字）",
  "overview_oneline": "全书一句话总览",
  "learning_thread": "学习主线：知识如何递进展开（忠于原文叙述逻辑，100-200字）",
  "summary": "全书综合摘要（200-400字）",
  "structure": [
    {{"title": "章节或模块名", "summary": "该部分主要内容（1-2句）", "children": []}}
  ],
  "knowledge_categories": {{
    "theoretical": "理论性知识（命题/定理/模型）简述，无则留空",
    "empirical": "实证性知识（数据/实验/案例）简述，无则留空",
    "normative": "规范性知识（应该如何/政策建议）简述，无则留空",
    "methodological": "方法论知识（研究方法/工具）简述，无则留空"
  }},
  "main_claims": [
    {{
      "claim": "核心主张（忠于原文，不添油加醋）",
      "stance": "明确论证 | 隐含假设 | 作者观点",
      "stance_marker": "《书名》明确论证 / 作者主张 / 研究表明（标清谁的主张）",
      "claim_grade": "unverified | low | medium | high（按KU grade+来源可信度综合判断）"
    }}
  ],
  "argument_structure": [
    {{
      "point": "论点",
      "evidence": [
        {{
          "text": "书中真正独立支撑该论点的内容（★必须独立，不能是point的复述或同义反复）",
          "grade": "low | medium | high"
        }}
      ],
      "boundary": "该论点的适用边界/前提条件（无则留空字符串）"
    }}
  ],
  "applicability": "全书知识适用边界：何时成立/何时失效（100-200字，忠于原文）",
  "core_takeaways": ["能记住的核心要点，每条一句话（5条以内）"],
  "key_concept_ku_ids": ["从上面KU列表中选最核心的ku_id，直接复制ID字符串"]
}}

关键约束：
1. stance_marker必须非空，明确指出"谁的主张"（不是"X是真理"）
2. argument_structure的evidence必须是书中独立内容，不能复述point（治假深度核心）
3. source_credibility保守：畅销书/商业方法论→low，学术教材可high，不确定偏低
4. claim_grade要和KU grade对齐：KU大量high的教材可标high；KU都是unverified的畅销书不能标high
5. structure必须是数组（不是字符串）"""

        resp = await llm(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=6000,
        )
        text = _extract_text(resp)
        cost.add_from_response(resp, model=config.llm_model)

        trail.record(event="llm_done", text_len=len(text))
        _notify(on_step, "analyze", "done")

        parsed = _parse_json(text)

        # ── 来源可信度 → 确定 grade 上限 ──────────────────────────────────
        source_credibility = parsed.get("source_credibility", {})
        if not isinstance(source_credibility, dict):
            source_credibility = {}
        book_type = source_credibility.get("book_type", "")
        grade_cap = _effective_grade_cap(config.doc_type, book_type)

        trail.record(event="grade_cap", book_type=book_type, grade_cap=grade_cap)

        # ── Post-process: enforce grade caps ──────────────────────────────
        main_claims = parsed.get("main_claims", [])
        for claim in main_claims:
            raw_grade = claim.get("claim_grade", "low")
            claim["claim_grade"] = _cap_grade(raw_grade, grade_cap)
            if not claim.get("stance_marker", "").strip():
                claim["stance_marker"] = f"（{config.book_substrate_id} 文本主张）"
            # normalise stance field
            if not claim.get("stance"):
                claim["stance"] = "作者观点"

        argument_structure = parsed.get("argument_structure", [])
        for arg in argument_structure:
            for ev in arg.get("evidence", []):
                raw_grade = ev.get("grade", "low")
                ev["grade"] = _cap_grade(raw_grade, grade_cap)
            if "boundary" not in arg:
                arg["boundary"] = ""

        # ── Normalise structure to array ──────────────────────────────────
        structure = _normalize_structure(parsed.get("structure", []))

        # ── Normalise scalar fields ───────────────────────────────────────
        knowledge_categories = parsed.get("knowledge_categories", {})
        if not isinstance(knowledge_categories, dict):
            knowledge_categories = {}
        for k in ("theoretical", "empirical", "normative", "methodological"):
            knowledge_categories.setdefault(k, "")

        core_takeaways = parsed.get("core_takeaways", [])
        if not isinstance(core_takeaways, list):
            core_takeaways = [str(core_takeaways)] if core_takeaways else []

        _notify(on_step, "report", "started")

        book_ku_id = f"book_{fingerprint[:8]}_{uuid.uuid4().hex[:6]}"

        findings = BookUnderstandingFindings(
            book_ku_id=book_ku_id,
            summary=parsed.get("summary", ""),
            source_credibility=source_credibility,
            problem_statement=parsed.get("problem_statement", ""),
            overview_oneline=parsed.get("overview_oneline", ""),
            learning_thread=parsed.get("learning_thread", ""),
            structure=structure,
            knowledge_categories=knowledge_categories,
            applicability=parsed.get("applicability", ""),
            core_takeaways=core_takeaways,
            main_claims=main_claims,
            argument_structure=argument_structure,
            key_concept_ku_ids=parsed.get("key_concept_ku_ids", []),
            doc_type=config.doc_type,
        )

        report_content = _build_report(findings, config, grade_cap)
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
        "source_credibility": {},
        "problem_statement": "",
        "overview_oneline": "",
        "learning_thread": "",
        "structure": [],
        "knowledge_categories": {},
        "applicability": "",
        "core_takeaways": [],
        "main_claims": [],
        "argument_structure": [],
        "key_concept_ku_ids": [],
    }


def _build_report(
    findings: BookUnderstandingFindings,
    config: BookUnderstandingConfig,
    grade_cap: str,
) -> str:
    sc = findings.source_credibility
    claims_md = "\n".join(
        f"- [{c.get('claim_grade','?')}|{c.get('stance','')}] {c.get('stance_marker','')} {c.get('claim','')}"
        for c in findings.main_claims
    )
    args_md = "\n".join(
        f"- **{a.get('point','')}**"
        + (f" _(边界: {a['boundary']})_" if a.get("boundary") else "")
        + "\n  " + "; ".join(
            f"[{e.get('grade','?')}] {e.get('text','')}"
            for e in a.get("evidence", [])
        )
        for a in findings.argument_structure
    )
    struct_md = "\n".join(
        f"{'  ' * 0}- **{s.get('title','')}**: {s.get('summary','')}"
        for s in findings.structure
    )
    kc = findings.knowledge_categories
    return (
        f"# Book Understanding: {config.book_substrate_id}\n\n"
        f"**doc_type**: {config.doc_type} | **grade_cap**: {grade_cap}  \n"
        f"**source**: {sc.get('book_type','')} ({sc.get('credibility_level','')}) — {sc.get('credibility_note','')}  \n"
        f"**synthesis_note**: {findings.synthesis_note}\n\n"
        f"## 核心问题\n\n{findings.problem_statement}\n\n"
        f"## 一句话总览\n\n{findings.overview_oneline}\n\n"
        f"## 学习主线\n\n{findings.learning_thread}\n\n"
        f"## Summary\n\n{findings.summary}\n\n"
        f"## 章节结构\n\n{struct_md or '(none)'}\n\n"
        f"## 知识分类\n\n"
        f"- 理论：{kc.get('theoretical','')}\n"
        f"- 实证：{kc.get('empirical','')}\n"
        f"- 规范：{kc.get('normative','')}\n"
        f"- 方法：{kc.get('methodological','')}\n\n"
        f"## Main Claims\n\n{claims_md or '(none)'}\n\n"
        f"## Argument Structure\n\n{args_md or '(none)'}\n\n"
        f"## 适用边界\n\n{findings.applicability}\n\n"
        f"## Core Takeaways\n\n"
        + "\n".join(f"- {t}" for t in findings.core_takeaways) + "\n"
    )


def _notify(on_step: Any, step: str, state: str) -> None:
    if on_step is not None:
        try:
            on_step(step=step, state=state)
        except Exception:
            pass
