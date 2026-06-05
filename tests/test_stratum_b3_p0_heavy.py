"""Tests for omodul-001/002: process_inbox_substrate, daily_digest_workflow."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub missing deps before any omodul / oprim / oskill imports
# ---------------------------------------------------------------------------
_STUB_MODULES = [
    "jinja2",
    "alembic",
    "alembic.command",
    "alembic.config",
    "alembic.runtime",
    "alembic.runtime.migration",
    "alembic.script",
    "sqlalchemy",
    "frontmatter",
]
for _mod in _STUB_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Import directly from submodule to avoid omodul.__init__ chain import issues
from omodul.process_inbox_substrate import (  # noqa: E402
    InboxConfig,
    InboxFindings,
    InboxInput,
    compute_fingerprint_for,
    process_inbox_substrate,
)
from omodul.daily_digest_workflow import (  # noqa: E402
    DailyDigestConfig,
    DailyDigestFindings,
    DailyDigestInput,
    daily_digest_workflow,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_CHECKSUM = "a" * 64  # 64-char hex string


def _inbox_config(**kw) -> InboxConfig:
    return InboxConfig(
        file_path=kw.pop("file_path", Path("/tmp/test.pdf")),
        file_checksum=kw.pop("file_checksum", _FAKE_CHECKSUM),
        user_id_hash=kw.pop("user_id_hash", "user-hash-abc"),
        **kw,
    )


def _digest_config(**kw) -> DailyDigestConfig:
    return DailyDigestConfig(
        digest_date=kw.pop("digest_date", date(2026, 6, 1)),
        user_id_hash=kw.pop("user_id_hash", "user-hash-abc"),
        **kw,
    )


def _mock_file_info(mime_type: str = "application/pdf", category: str = "document") -> MagicMock:
    m = MagicMock()
    m.mime_type = mime_type
    m.category = category
    return m


def _mock_page(text: str = "Hello world") -> MagicMock:
    m = MagicMock()
    m.text = text
    return m


def _mock_parsed_doc(pages: list | None = None) -> MagicMock:
    m = MagicMock()
    m.pages = pages or [_mock_page()]
    return m


def _mock_structure(headings: list | None = None, word_count: int = 100) -> MagicMock:
    m = MagicMock()
    m.headings = headings if headings is not None else [{"level": 1, "text": "Title"}]
    m.word_count = word_count
    m.pages = [_mock_page()]
    return m


def _mock_classify(medium: str = "paper", confidence: float = 0.9) -> MagicMock:
    m = MagicMock()
    m.medium = medium
    m.confidence = confidence
    return m


def _mock_summarize_result(summary: str = "Today's digest") -> MagicMock:
    m = MagicMock()
    m.summary = summary
    m.tokens_used = 10
    m.provider = "qwen3"
    return m


def _inbox_patches(
    substrate_id: str = "substrate-id-123",
    derivative_id: str = "derivative-id-456",
    file_info: MagicMock | None = None,
    parsed_doc: MagicMock | None = None,
    structure: MagicMock | None = None,
    classify: MagicMock | None = None,
) -> list:
    """Return list of patch context managers for process_inbox_substrate success path."""
    fi = file_info or _mock_file_info()
    pd = parsed_doc or _mock_parsed_doc()
    st = structure or _mock_structure()
    cl = classify or _mock_classify()
    return [
        patch("oprim.file_type_detector.file_type_detector", return_value=fi),
        patch("oprim.file_parser_pdf.file_parser_pdf", return_value=pd),
        patch("oprim.document_structure_extractor.document_structure_extractor", return_value=st),
        patch("oprim._document_types.ParsedDocument", MagicMock),
        patch("oskill.knowledge.classify_inbox_file.classify_inbox_file", return_value=cl),
        patch("asyncio.run", side_effect=[substrate_id, derivative_id]),
    ]


# ---------------------------------------------------------------------------
# omodul-001: process_inbox_substrate
# ---------------------------------------------------------------------------


class TestProcessInboxSubstrate:
    def test_success_status_completed_and_substrate_id(self, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf content")
        config = _inbox_config(file_path=test_file)
        input_data = InboxInput()

        fi = _mock_file_info()
        pd_doc = _mock_parsed_doc()
        st = _mock_structure()
        cl = _mock_classify()

        with (
            patch("oprim.file_type_detector.file_type_detector", return_value=fi),
            patch("oprim.file_parser_pdf.file_parser_pdf", return_value=pd_doc),
            patch(
                "oprim.document_structure_extractor.document_structure_extractor", return_value=st
            ),
            patch("oprim._document_types.ParsedDocument", MagicMock),
            patch("oskill.knowledge.classify_inbox_file.classify_inbox_file", return_value=cl),
            patch("asyncio.run", side_effect=["substrate-id-123", "derivative-id-456"]),
        ):
            result = process_inbox_substrate(config, input_data, tmp_path)

        assert result["status"] == "completed"
        assert result["findings"] is not None
        assert result["findings"].substrate_id == "substrate-id-123"
        assert result["findings"].medium == "paper"

    def test_fingerprint_present_and_64_chars(self, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf content")
        config = _inbox_config(file_path=test_file)
        input_data = InboxInput()

        fi = _mock_file_info()
        pd_doc = _mock_parsed_doc()
        st = _mock_structure()
        cl = _mock_classify()

        with (
            patch("oprim.file_type_detector.file_type_detector", return_value=fi),
            patch("oprim.file_parser_pdf.file_parser_pdf", return_value=pd_doc),
            patch(
                "oprim.document_structure_extractor.document_structure_extractor", return_value=st
            ),
            patch("oprim._document_types.ParsedDocument", MagicMock),
            patch("oskill.knowledge.classify_inbox_file.classify_inbox_file", return_value=cl),
            patch("asyncio.run", side_effect=["substrate-id-123", "derivative-id-456"]),
        ):
            result = process_inbox_substrate(config, input_data, tmp_path)

        assert result["fingerprint"] is not None
        assert len(result["fingerprint"]) == 64

    def test_decision_trail_json_written(self, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf content")
        config = _inbox_config(file_path=test_file)
        input_data = InboxInput()

        fi = _mock_file_info()
        pd_doc = _mock_parsed_doc()
        st = _mock_structure()
        cl = _mock_classify()

        with (
            patch("oprim.file_type_detector.file_type_detector", return_value=fi),
            patch("oprim.file_parser_pdf.file_parser_pdf", return_value=pd_doc),
            patch(
                "oprim.document_structure_extractor.document_structure_extractor", return_value=st
            ),
            patch("oprim._document_types.ParsedDocument", MagicMock),
            patch("oskill.knowledge.classify_inbox_file.classify_inbox_file", return_value=cl),
            patch("asyncio.run", side_effect=["substrate-id-123", "derivative-id-456"]),
        ):
            process_inbox_substrate(config, input_data, tmp_path)

        trail_file = tmp_path / "decision_trail.json"
        assert trail_file.exists()
        data = json.loads(trail_file.read_text())
        assert data["omodul_name"] == "process_inbox_substrate"
        assert data["status"] == "completed"

    def test_report_path_exists_on_success(self, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf content")
        config = _inbox_config(file_path=test_file)
        input_data = InboxInput()

        fi = _mock_file_info()
        pd_doc = _mock_parsed_doc()
        st = _mock_structure()
        cl = _mock_classify()

        with (
            patch("oprim.file_type_detector.file_type_detector", return_value=fi),
            patch("oprim.file_parser_pdf.file_parser_pdf", return_value=pd_doc),
            patch(
                "oprim.document_structure_extractor.document_structure_extractor", return_value=st
            ),
            patch("oprim._document_types.ParsedDocument", MagicMock),
            patch("oskill.knowledge.classify_inbox_file.classify_inbox_file", return_value=cl),
            patch("asyncio.run", side_effect=["substrate-id-123", "derivative-id-456"]),
        ):
            result = process_inbox_substrate(config, input_data, tmp_path)

        assert result["report_path"] is not None
        assert result["report_path"].exists()

    def test_failure_status_failed_no_raise(self, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf content")
        config = _inbox_config(file_path=test_file)
        input_data = InboxInput()

        with patch(
            "oprim.file_type_detector.file_type_detector",
            side_effect=RuntimeError("MIME detection failed"),
        ):
            result = process_inbox_substrate(config, input_data, tmp_path)

        assert result["status"] == "failed"
        assert result["error"] is not None
        assert "MIME detection failed" in result["error"]["error_message"]
        assert result["findings"] is None

    def test_decision_trail_written_even_on_failure(self, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf content")
        config = _inbox_config(file_path=test_file)
        input_data = InboxInput()

        with patch(
            "oprim.file_type_detector.file_type_detector",
            side_effect=RuntimeError("MIME detection failed"),
        ):
            process_inbox_substrate(config, input_data, tmp_path)

        trail_file = tmp_path / "decision_trail.json"
        assert trail_file.exists()
        data = json.loads(trail_file.read_text())
        assert data["status"] == "failed"

    def test_derivative_ids_collected(self, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf content")
        config = _inbox_config(file_path=test_file, generate_derivatives=["summary", "outline"])
        input_data = InboxInput()

        fi = _mock_file_info()
        pd_doc = _mock_parsed_doc()
        st = _mock_structure()
        cl = _mock_classify()

        with (
            patch("oprim.file_type_detector.file_type_detector", return_value=fi),
            patch("oprim.file_parser_pdf.file_parser_pdf", return_value=pd_doc),
            patch(
                "oprim.document_structure_extractor.document_structure_extractor", return_value=st
            ),
            patch("oprim._document_types.ParsedDocument", MagicMock),
            patch("oskill.knowledge.classify_inbox_file.classify_inbox_file", return_value=cl),
            patch("asyncio.run", side_effect=["substrate-id-123", "deriv-1", "deriv-2"]),
        ):
            result = process_inbox_substrate(config, input_data, tmp_path)

        assert result["status"] == "completed"
        assert len(result["findings"].derivative_ids) == 2

    def test_medium_hint_overrides_classify(self, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf content")
        config = _inbox_config(file_path=test_file, medium_hint="book")
        input_data = InboxInput()

        fi = _mock_file_info()
        pd_doc = _mock_parsed_doc()
        st = _mock_structure()
        cl = _mock_classify(medium="paper")  # classify says paper, but hint overrides

        with (
            patch("oprim.file_type_detector.file_type_detector", return_value=fi),
            patch("oprim.file_parser_pdf.file_parser_pdf", return_value=pd_doc),
            patch(
                "oprim.document_structure_extractor.document_structure_extractor", return_value=st
            ),
            patch("oprim._document_types.ParsedDocument", MagicMock),
            patch("oskill.knowledge.classify_inbox_file.classify_inbox_file", return_value=cl),
            patch("asyncio.run", side_effect=["substrate-id-123", "derivative-id-456"]),
        ):
            result = process_inbox_substrate(config, input_data, tmp_path)

        assert result["findings"].medium == "book"

    def test_decision_trail_has_multiple_steps(self, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf content")
        config = _inbox_config(file_path=test_file)
        input_data = InboxInput()

        fi = _mock_file_info()
        pd_doc = _mock_parsed_doc()
        st = _mock_structure()
        cl = _mock_classify()

        with (
            patch("oprim.file_type_detector.file_type_detector", return_value=fi),
            patch("oprim.file_parser_pdf.file_parser_pdf", return_value=pd_doc),
            patch(
                "oprim.document_structure_extractor.document_structure_extractor", return_value=st
            ),
            patch("oprim._document_types.ParsedDocument", MagicMock),
            patch("oskill.knowledge.classify_inbox_file.classify_inbox_file", return_value=cl),
            patch("asyncio.run", side_effect=["substrate-id-123", "derivative-id-456"]),
        ):
            result = process_inbox_substrate(config, input_data, tmp_path)

        steps = result["decision_trail"]["steps"]
        assert (
            len(steps) >= 4
        )  # file_type_detector, parser, structure, classify, ingest, derivative

    def test_cost_is_zero(self, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf content")
        config = _inbox_config(file_path=test_file)
        input_data = InboxInput()

        fi = _mock_file_info()
        pd_doc = _mock_parsed_doc()
        st = _mock_structure()
        cl = _mock_classify()

        with (
            patch("oprim.file_type_detector.file_type_detector", return_value=fi),
            patch("oprim.file_parser_pdf.file_parser_pdf", return_value=pd_doc),
            patch(
                "oprim.document_structure_extractor.document_structure_extractor", return_value=st
            ),
            patch("oprim._document_types.ParsedDocument", MagicMock),
            patch("oskill.knowledge.classify_inbox_file.classify_inbox_file", return_value=cl),
            patch("asyncio.run", side_effect=["substrate-id-123", "derivative-id-456"]),
        ):
            result = process_inbox_substrate(config, input_data, tmp_path)

        assert result["cost_usd"] == 0.0

    def test_process_inbox_substrate_passes_user_id_hash(self, tmp_path):
        """ingest_substrate must receive user_id_hash matching InboxConfig.user_id_hash."""
        import asyncio as _asyncio

        from oskill.ingest_substrate import IngestResult

        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf content")
        config = _inbox_config(file_path=test_file, user_id_hash="uid-check-789")
        input_data = InboxInput()

        mock_ingest = AsyncMock(return_value=IngestResult(substrate_id="sub-xyz", medium="paper"))
        call_idx = [0]

        def _fake_run(coro):
            call_idx[0] += 1
            if call_idx[0] == 1:
                loop = _asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()
            coro.close()
            return "deriv-id-xyz"

        with (
            patch("oprim.file_type_detector.file_type_detector", return_value=_mock_file_info()),
            patch("oprim.file_parser_pdf.file_parser_pdf", return_value=_mock_parsed_doc()),
            patch(
                "oprim.document_structure_extractor.document_structure_extractor",
                return_value=_mock_structure(),
            ),
            patch("oprim._document_types.ParsedDocument", MagicMock),
            patch(
                "oskill.knowledge.classify_inbox_file.classify_inbox_file",
                return_value=_mock_classify(),
            ),
            patch("oskill.ingest_substrate.ingest_substrate", mock_ingest),
            patch("asyncio.run", side_effect=_fake_run),
        ):
            process_inbox_substrate(config, input_data, tmp_path)

        mock_ingest.assert_called_once()
        assert mock_ingest.call_args.kwargs["user_id_hash"] == "uid-check-789"


# ---------------------------------------------------------------------------
# omodul-002: daily_digest_workflow
# ---------------------------------------------------------------------------


class TestDailyDigestWorkflow:
    def _mock_search_result(
        self, title: str = "Item 1", highlight: str = "Summary of item 1"
    ) -> MagicMock:
        m = MagicMock()
        m.title = title
        m.highlight = highlight
        return m

    def test_success_status_completed(self, tmp_path):
        config = _digest_config()
        input_data = DailyDigestInput(custom_notes=["Note A"])

        summarize_result = _mock_summarize_result()

        with (
            patch("asyncio.run", return_value=[]),
            patch("oprim.llm_summarize.llm_summarize", return_value=summarize_result),
        ):
            result = daily_digest_workflow(config, input_data, tmp_path)

        assert result["status"] == "completed"
        assert result["findings"] is not None

    def test_findings_digest_text_present(self, tmp_path):
        config = _digest_config()
        input_data = DailyDigestInput(custom_notes=["Note A", "Note B"])

        summarize_result = _mock_summarize_result(summary="Bullet 1\nBullet 2")

        with (
            patch("asyncio.run", return_value=[]),
            patch("oprim.llm_summarize.llm_summarize", return_value=summarize_result),
        ):
            result = daily_digest_workflow(config, input_data, tmp_path)

        assert result["findings"].digest_text == "Bullet 1\nBullet 2"

    def test_report_path_created(self, tmp_path):
        config = _digest_config()
        input_data = DailyDigestInput()

        summarize_result = _mock_summarize_result()

        with (
            patch("asyncio.run", return_value=[]),
            patch("oprim.llm_summarize.llm_summarize", return_value=summarize_result),
        ):
            result = daily_digest_workflow(config, input_data, tmp_path)

        assert result["report_path"] is not None
        assert result["report_path"].exists()

    def test_failure_no_raise(self, tmp_path):
        config = _digest_config()
        input_data = DailyDigestInput()

        with patch("asyncio.run", side_effect=RuntimeError("search backend down")):
            result = daily_digest_workflow(config, input_data, tmp_path)

        assert result["status"] == "failed"
        assert result["error"] is not None
        assert "search backend down" in result["error"]["error_message"]
        assert result["findings"] is None

    def test_fingerprint_non_null(self, tmp_path):
        config = _digest_config()
        input_data = DailyDigestInput()

        summarize_result = _mock_summarize_result()

        with (
            patch("asyncio.run", return_value=[]),
            patch("oprim.llm_summarize.llm_summarize", return_value=summarize_result),
        ):
            result = daily_digest_workflow(config, input_data, tmp_path)

        assert result["fingerprint"] is not None
        assert len(result["fingerprint"]) == 64

    def test_decision_trail_is_none(self, tmp_path):
        """daily_digest_workflow has no decision_trail pillar."""
        config = _digest_config()
        input_data = DailyDigestInput()

        summarize_result = _mock_summarize_result()

        with (
            patch("asyncio.run", return_value=[]),
            patch("oprim.llm_summarize.llm_summarize", return_value=summarize_result),
        ):
            result = daily_digest_workflow(config, input_data, tmp_path)

        assert result["decision_trail"] is None

    def test_with_substrate_ids_generates_note(self, tmp_path):
        config = _digest_config()
        input_data = DailyDigestInput(
            recent_substrate_ids=["sub-1", "sub-2"],
            custom_notes=["Note X"],
        )

        summarize_result = _mock_summarize_result()

        # asyncio.run called twice: once for hybrid_search, once for generate_derivative
        with (
            patch("asyncio.run", side_effect=[[], "note-id-789"]),
            patch("oprim.llm_summarize.llm_summarize", return_value=summarize_result),
        ):
            result = daily_digest_workflow(config, input_data, tmp_path)

        assert result["status"] == "completed"
        assert result["findings"].note_id == "note-id-789"

    def test_item_count_reflects_deduplicated_content(self, tmp_path):
        config = _digest_config()
        input_data = DailyDigestInput(custom_notes=["A", "B", "C"])

        summarize_result = _mock_summarize_result()

        with (
            patch("asyncio.run", return_value=[]),
            patch("oprim.llm_summarize.llm_summarize", return_value=summarize_result),
        ):
            result = daily_digest_workflow(config, input_data, tmp_path)

        assert result["findings"].item_count == 3

    def test_fingerprint_differs_for_different_dates(self, tmp_path):
        inp = DailyDigestInput()
        summarize_result = _mock_summarize_result()

        with (
            patch("asyncio.run", return_value=[]),
            patch("oprim.llm_summarize.llm_summarize", return_value=summarize_result),
        ):
            r1 = daily_digest_workflow(_digest_config(digest_date=date(2026, 6, 1)), inp, tmp_path)
            r2 = daily_digest_workflow(_digest_config(digest_date=date(2026, 6, 2)), inp, tmp_path)

        assert r1["fingerprint"] != r2["fingerprint"]
