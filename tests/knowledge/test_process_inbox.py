"""Tests for omodul.knowledge.process_inbox."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omodul.knowledge.process_inbox import ProcessInboxResult, _move_to_archive, process_inbox
from oskill.knowledge.classify_inbox_file import ClassifyResult
from oskill.knowledge.ingest_substrate import IngestResult


def _make_classify(layer="extension", medium="markdown_note", confidence=0.95):
    return ClassifyResult(
        medium=medium, confidence=confidence, layer=layer,
        reason="test", candidates=[(medium, confidence)],
    )


def _make_ingest(substrate_id="SUBID0000001234567890123456", medium="markdown_note"):
    return IngestResult(substrate_id=substrate_id, medium=medium)


class TestProcessInboxHelpers:
    def test_move_to_archive_creates_dir(self, tmp_path):
        src = tmp_path / "file.md"
        src.write_text("hello")
        archive = tmp_path / "_archive"
        _move_to_archive(src, archive)
        assert not src.exists()
        assert (archive / "file.md").exists()

    def test_move_to_archive_collision_renames(self, tmp_path):
        src = tmp_path / "file.md"
        src.write_text("hello")
        archive = tmp_path / "_archive"
        archive.mkdir()
        (archive / "file.md").write_text("existing")
        _move_to_archive(src, archive)
        assert not src.exists()
        remaining = list(archive.glob("file*"))
        assert len(remaining) == 2


class TestProcessInbox:
    async def test_empty_inbox_returns_empty(self, stratum_home):
        inbox = stratum_home / "inbox"
        inbox.mkdir()
        result = await process_inbox(inbox)
        assert result.processed == []
        assert result.failed == []
        assert result.needs_review == []

    async def test_skips_hidden_files(self, stratum_home):
        inbox = stratum_home / "inbox"
        inbox.mkdir()
        (inbox / ".hidden").write_text("ignore me")
        result = await process_inbox(inbox)
        assert result.processed == []
        assert result.failed == []

    async def test_skips_directories(self, stratum_home):
        inbox = stratum_home / "inbox"
        inbox.mkdir()
        (inbox / "subdir").mkdir()
        result = await process_inbox(inbox)
        assert result.processed == []
        assert result.failed == []

    async def test_processes_single_md_file(self, stratum_home, simple_md):
        inbox = stratum_home / "inbox"
        inbox.mkdir()
        target = inbox / simple_md.name
        target.write_text(simple_md.read_text())

        with patch("omodul.knowledge.process_inbox.classify_inbox_file", return_value=_make_classify()):
            with patch("omodul.knowledge.process_inbox.ingest_substrate", new=AsyncMock(return_value=_make_ingest())):
                result = await process_inbox(inbox, archive_after_process=False)

        assert len(result.processed) == 1
        assert result.failed == []
        assert result.needs_review == []

    async def test_archive_moves_processed_file(self, stratum_home, simple_md):
        inbox = stratum_home / "inbox"
        inbox.mkdir()
        target = inbox / simple_md.name
        target.write_text(simple_md.read_text())

        with patch("omodul.knowledge.process_inbox.classify_inbox_file", return_value=_make_classify()):
            with patch("omodul.knowledge.process_inbox.ingest_substrate", new=AsyncMock(return_value=_make_ingest())):
                result = await process_inbox(inbox, archive_after_process=True)

        assert len(result.processed) == 1
        assert not target.exists()
        assert (inbox / "_archive" / simple_md.name).exists()

    async def test_no_archive_when_disabled(self, stratum_home, simple_md):
        inbox = stratum_home / "inbox"
        inbox.mkdir()
        target = inbox / simple_md.name
        target.write_text(simple_md.read_text())

        with patch("omodul.knowledge.process_inbox.classify_inbox_file", return_value=_make_classify()):
            with patch("omodul.knowledge.process_inbox.ingest_substrate", new=AsyncMock(return_value=_make_ingest())):
                await process_inbox(inbox, archive_after_process=False)

        assert target.exists()

    async def test_failed_file_does_not_block_others(self, stratum_home):
        inbox = stratum_home / "inbox"
        inbox.mkdir()
        (inbox / "good.md").write_text("# Good")
        (inbox / "bad.md").write_text("# Bad")

        def classify_side_effect(path, **kwargs):
            if "bad" in path.name:
                raise RuntimeError("classify error")
            return _make_classify()

        with patch("omodul.knowledge.process_inbox.classify_inbox_file", side_effect=classify_side_effect):
            with patch("omodul.knowledge.process_inbox.ingest_substrate", new=AsyncMock(return_value=_make_ingest())):
                result = await process_inbox(inbox, archive_after_process=False)

        assert len(result.processed) == 1
        assert len(result.failed) == 1
        assert "bad.md" in result.failed[0]["path"]

    async def test_needs_review_not_ingested(self, stratum_home, simple_md):
        inbox = stratum_home / "inbox"
        inbox.mkdir()
        target = inbox / simple_md.name
        target.write_text(simple_md.read_text())

        with patch("omodul.knowledge.process_inbox.classify_inbox_file",
                   return_value=_make_classify(layer="needs_review", medium=None, confidence=0.2)):
            with patch("omodul.knowledge.process_inbox.ingest_substrate", new=AsyncMock()) as mock_ingest:
                result = await process_inbox(inbox, archive_after_process=False)

        assert result.processed == []
        assert len(result.needs_review) == 1
        mock_ingest.assert_not_called()

    async def test_needs_review_archived_when_enabled(self, stratum_home, simple_md):
        inbox = stratum_home / "inbox"
        inbox.mkdir()
        target = inbox / simple_md.name
        target.write_text(simple_md.read_text())

        with patch("omodul.knowledge.process_inbox.classify_inbox_file",
                   return_value=_make_classify(layer="needs_review", medium=None, confidence=0.2)):
            with patch("omodul.knowledge.process_inbox.ingest_substrate", new=AsyncMock()):
                result = await process_inbox(inbox, archive_after_process=True)

        assert len(result.needs_review) == 1
        assert not target.exists()
        assert (inbox / "_archive" / simple_md.name).exists()

    async def test_two_files_both_processed(self, inbox_with_files):
        with patch("omodul.knowledge.process_inbox.classify_inbox_file", return_value=_make_classify()):
            with patch("omodul.knowledge.process_inbox.ingest_substrate",
                       new=AsyncMock(side_effect=[_make_ingest("ID1"), _make_ingest("ID2")])):
                result = await process_inbox(inbox_with_files, archive_after_process=False)

        assert len(result.processed) == 2
        assert result.failed == []

    async def test_ingest_failure_goes_to_failed(self, stratum_home, simple_md):
        inbox = stratum_home / "inbox"
        inbox.mkdir()
        (inbox / simple_md.name).write_text(simple_md.read_text())

        with patch("omodul.knowledge.process_inbox.classify_inbox_file", return_value=_make_classify()):
            with patch("omodul.knowledge.process_inbox.ingest_substrate",
                       new=AsyncMock(side_effect=RuntimeError("ingest failed"))):
                result = await process_inbox(inbox, archive_after_process=False)

        assert result.processed == []
        assert len(result.failed) == 1
        assert "ingest failed" in result.failed[0]["error"]
