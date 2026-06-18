"""omodul.process_inbox_substrate — 文件入库主流程。

Pillars: fingerprint + decision_trail + report
Composition (oprim + oskill):
  - oprim.file_type_detector → MIME type
  - oprim.file_parser_pdf/epub/html/markdown/plaintext → ParsedDocument
  - oprim.document_structure_extractor → DocumentStructure
  - oskill.classify_inbox_file (already in oskill library)
  - oskill.ingest_substrate (already in oskill library)
  - oskill.generate_derivative (already in oskill library, async)
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report


class InboxConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "process_inbox_substrate"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}
    _fingerprint_fields: ClassVar[set[str]] = {"file_checksum", "medium_hint", "user_id_hash"}

    file_path: Path  # Path to the uploaded temp file
    file_checksum: str  # SHA-256 of the file (caller computed)
    user_id_hash: str
    medium_hint: str | None = None
    auto_classify: bool = True
    generate_derivatives: list[str] = ["summary"]
    corpus_id: str = "default"


class InboxInput(BaseModel):
    metadata_override: dict[str, Any] = {}


class InboxFindings(BaseModel):
    substrate_id: str
    substrate_ids: list[str] = []   # 套装时含多个，单本时含1个
    medium: str
    derivative_ids: list[str] = []
    classification_confidence: float = 0.0
    page_count: int = 0
    heading_count: int = 0
    is_bundle: bool = False          # True 表示套装，已拆分为多个 substrate
    parse_quality: str = "ok"        # "ok"|"empty"|"scanned"|"garbled"
    is_duplicate: bool = False       # True 表示已有相同 file_hash 的 substrate
    duplicate_of: str | None = None  # 重复时指向已有 substrate_id


def process_inbox_substrate(
    config: InboxConfig,
    input_data: InboxInput,
    output_dir: Path,
) -> dict[str, Any]:
    """Parse, classify, and ingest a file into the substrate system.

    Internal oprim composition:
      - oprim.file_type_detector (MIME detection)
      - oprim.file_parser_pdf/epub/html/markdown/plaintext (parsing)
      - oprim.document_structure_extractor (structure extraction)

    Internal oskill composition (depth-1):
      - oskill.classify_inbox_file (medium classification)
      - oskill.ingest_substrate (async, DB+vector store ingest)
      - oskill.generate_derivative (async, summary generation)
    """
    started_at = datetime.now(UTC)
    enabled = config._enabled_pillars
    fingerprint = compute_fingerprint(config, input_data) if "fingerprint" in enabled else None
    cost_tracker = CostTracker(budget_usd=config.budget_usd) if "cost" in enabled else None
    trail_steps: list[dict[str, Any]] = [] if "decision_trail" in enabled else []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings: InboxFindings | None = None
    dt: dict[str, Any] = {}

    try:
        # Stage 1: Detect file type
        step_start = datetime.now(UTC)
        from oprim.file_type_detector import file_type_detector

        file_info = file_type_detector(file_path=config.file_path)
        record_step(
            trail_steps=trail_steps,
            on_step=None,
            layer="oprim",
            callable_name="file_type_detector",
            inputs_summary={"file_path": str(config.file_path)},
            outputs_summary={"mime_type": file_info.mime_type, "category": file_info.category},
            started_at=step_start,
        )

        # Stage 2: Parse based on file type
        step_start = datetime.now(UTC)
        parsed_doc = _stage_parse(config.file_path, file_info.mime_type)
        record_step(
            trail_steps=trail_steps,
            on_step=None,
            layer="oprim",
            callable_name=f"file_parser_{file_info.category}",
            inputs_summary={"mime_type": file_info.mime_type},
            outputs_summary={"page_count": len(getattr(parsed_doc, "pages", []))},
            started_at=step_start,
        )

        # Stage 2b: Parse quality check
        _all_text = " ".join(
            getattr(p, "text", "") for p in getattr(parsed_doc, "pages", [])
        )
        _text_len = len(_all_text.strip())
        _ufffd_ratio = _all_text.count("\ufffd") / max(_text_len, 1) if _text_len else 0
        _pic_ratio = _all_text.count("[image]") / max(_text_len // 10, 1) if _text_len else 0

        if _text_len < 500:
            _parse_quality = "empty"
        elif _ufffd_ratio > 0.30:
            _parse_quality = "garbled"
        elif _pic_ratio > 0.50:
            _parse_quality = "scanned"
        else:
            _parse_quality = "ok"

        # Stage 3: Extract structure
        step_start = datetime.now(UTC)
        from oprim._document_types import ParsedDocument
        from oprim.document_structure_extractor import document_structure_extractor

        # If parsed_doc is not ParsedDocument (e.g. Markdown/Plaintext), wrap it
        if not isinstance(parsed_doc, ParsedDocument):
            from oprim._document_types import Page
            from oprim._document_types import ParsedDocument as PD

            pages_text = getattr(parsed_doc, "body", "") or getattr(parsed_doc, "paragraphs", [])
            if isinstance(pages_text, list):
                pages_text = "\n\n".join(pages_text)
            doc_for_structure = PD(pages=[Page(page_number=1, text=str(pages_text))])
        else:
            doc_for_structure = parsed_doc
        structure = document_structure_extractor(parsed_doc=doc_for_structure)
        record_step(
            trail_steps=trail_steps,
            on_step=None,
            layer="oprim",
            callable_name="document_structure_extractor",
            inputs_summary={"page_count": len(doc_for_structure.pages)},
            outputs_summary={
                "heading_count": len(structure.headings),
                "word_count": structure.word_count,
            },
            started_at=step_start,
        )

        # Stage 4: Classify (oskill)
        step_start = datetime.now(UTC)
        from oskill.knowledge.classify_inbox_file import ClassifyResult, classify_inbox_file

        classify_result: ClassifyResult = classify_inbox_file(
            path=config.file_path,
            use_llm=False,
        )
        medium: str = config.medium_hint or str(classify_result.medium or "other")
        confidence = classify_result.confidence if hasattr(classify_result, "confidence") else 0.8
        record_step(
            trail_steps=trail_steps,
            on_step=None,
            layer="oskill",
            callable_name="classify_inbox_file",
            inputs_summary={"use_llm": False},
            outputs_summary={"medium": medium, "confidence": confidence},
            started_at=step_start,
        )

        # Stage 5: Ingest substrate(s)
        # If parsed_doc is a list[EpubBook], it's a bundle — ingest each separately
        from oskill.ingest_substrate import ingest_substrate
        from oprim._epub_toc_split import EpubBook

        step_start = datetime.now(UTC)
        if isinstance(parsed_doc, list) and parsed_doc and isinstance(parsed_doc[0], EpubBook):
            # Bundle: create N independent substrates
            substrate_ids = []
            for book in parsed_doc:
                s_id = asyncio.run(
                    ingest_substrate(
                        path=config.file_path,
                        source={
                            "corpus_id": config.corpus_id,
                            "user_id_hash": config.user_id_hash,
                            "book_title": book.book_title,
                            **input_data.metadata_override,
                        },
                        user_id_hash=config.user_id_hash,
                        user_hint={"medium": medium, "book_title": book.book_title},
                        content_override=book.content,
                        metadata_override=book.metadata,
                    )
                )
                substrate_ids.append(str(s_id))
            substrate_id = substrate_ids[0]  # primary for findings
            record_step(
                trail_steps=trail_steps,
                on_step=None,
                layer="oskill",
                callable_name="ingest_substrate (bundle)",
                inputs_summary={"corpus_id": config.corpus_id, "book_count": len(parsed_doc)},
                outputs_summary={"substrate_ids": [s[:12]+"..." for s in substrate_ids]},
                started_at=step_start,
            )
        else:
            substrate_id = asyncio.run(
                ingest_substrate(
                    path=config.file_path,
                    source={
                        "corpus_id": config.corpus_id,
                        "user_id_hash": config.user_id_hash,
                        **input_data.metadata_override,
                    },
                    user_id_hash=config.user_id_hash,
                    user_hint={"medium": medium} if medium else None,
                )
            )
            substrate_ids = [str(substrate_id)]
            record_step(
                trail_steps=trail_steps,
                on_step=None,
                layer="oskill",
                callable_name="ingest_substrate",
                inputs_summary={"corpus_id": config.corpus_id, "medium": medium},
                outputs_summary={"substrate_id": str(substrate_id)[:12] + "..."},
                started_at=step_start,
            )

        # Stage 6: Generate derivatives
        derivative_ids: list[str] = []
        from oskill.knowledge.generate_derivative import generate_derivative

        for deriv_type in config.generate_derivatives:
            step_start = datetime.now(UTC)
            d_id = asyncio.run(
                generate_derivative(
                    str(substrate_id),
                    config.file_path,
                    medium,
                )
            )
            if d_id:
                derivative_ids.append(str(d_id))
            record_step(
                trail_steps=trail_steps,
                on_step=None,
                layer="oskill",
                callable_name="generate_derivative",
                inputs_summary={"type": deriv_type},
                outputs_summary={"derivative_id": str(d_id)[:12] + "..." if d_id else None},
                started_at=step_start,
            )

        # Check if ingest_substrate returned duplicate_of
        # substrate_id here is IngestResult object (asyncio.run returns IngestResult)
        _is_dup = bool(getattr(substrate_id, "duplicate_of", None))
        _dup_of = str(substrate_id.duplicate_of) if _is_dup else None
        # Normalize substrate_id to string for downstream use
        _substrate_id_str = str(getattr(substrate_id, "substrate_id", substrate_id))

        findings = InboxFindings(
            substrate_id=_substrate_id_str,
            substrate_ids=substrate_ids,
            medium=medium,
            derivative_ids=derivative_ids,
            classification_confidence=confidence,
            page_count=len(doc_for_structure.pages),
            heading_count=len(structure.headings),
            is_bundle=len(substrate_ids) > 1,
            parse_quality=_parse_quality,
            is_duplicate=_is_dup,
            duplicate_of=_dup_of,
        )

    except Exception as e:
        error_info = {
            "error_class": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }
        status = "failed"

    finally:
        if "decision_trail" in enabled:
            _cost_tracker = cost_tracker or CostTracker(budget_usd=0.0)
            dt = build_decision_trail(
                fingerprint=fingerprint or "",
                config=config,
                input_data=input_data,
                trail_steps=trail_steps,
                started_at=started_at,
                status=status,
                error=error_info,
                cost_tracker=_cost_tracker,
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "decision_trail.json").write_text(
                json.dumps(dt, indent=2, ensure_ascii=False, default=str)
            )

    report_path: Path | None = None
    if "report" in enabled and status == "completed" and findings:
        report_path = write_markdown_report(
            output_dir=output_dir,
            omodul_name=config._omodul_name,
            fingerprint=fingerprint or "",
            config=config,
            findings=findings,
            decision_trail=dt,
            cost_tracker=cost_tracker or CostTracker(budget_usd=0.0),
            status=status,
        )

    return {
        "findings": findings,
        "status": status,
        "error": error_info,
        "fingerprint": fingerprint,
        "decision_trail": dt if "decision_trail" in enabled else None,
        "report_path": report_path,
        "cost_usd": cost_tracker.total_usd if cost_tracker else 0.0,
    }


def _stage_parse(file_path: Path, mime_type: str) -> Any:
    """Dispatch to the correct file parser based on MIME type."""
    if "pdf" in mime_type:
        from oprim.file_parser_pdf import file_parser_pdf

        return file_parser_pdf(file_path=file_path)
    elif "epub" in mime_type or "epub+zip" in mime_type:
        from oprim.epub_toc_split import epub_toc_split
        from oprim.file_parser_epub import file_parser_epub

        books = epub_toc_split(file_path=file_path)
        if len(books) > 1:
            # Bundle detected — return list for multi-substrate handling
            return books  # list[EpubBook]
        return file_parser_epub(file_path=file_path)
    elif "html" in mime_type:
        with open(file_path) as f:
            html_content = f.read()
        from oprim.file_parser_html import file_parser_html

        return file_parser_html(html_content=html_content)
    elif mime_type in ("text/markdown",) or file_path.suffix.lower() in (".md", ".markdown"):
        from oprim.file_parser_markdown import file_parser_markdown

        return file_parser_markdown(file_path=file_path)
    else:
        from oprim.file_parser_plaintext import file_parser_plaintext

        return file_parser_plaintext(file_path=file_path)


def compute_fingerprint_for(config: InboxConfig, input_data: InboxInput) -> str:
    return compute_fingerprint(config, input_data)
