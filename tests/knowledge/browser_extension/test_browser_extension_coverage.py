"""Edge case coverage tests to push above 90%."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from omodul.knowledge.browser_extension.page_capture import extract_main_content
from omodul.knowledge.browser_extension.auth import init_token
from omodul.knowledge.browser_extension.server import _run_ingest, app
from fastapi.testclient import TestClient


# ── page_capture: fallback path with no title → returns plain text (line 28) ──


class TestPageCapturePlainTextFallback:
    def test_fallback_no_title_returns_plain_text(self):
        """Hit line 28: lxml fallback returns text without title prefix."""
        bad_doc = MagicMock()
        bad_doc.summary.side_effect = RuntimeError("readability error")
        with patch(
            "omodul.knowledge.browser_extension.page_capture.Document", return_value=bad_doc
        ):
            html = "<html><body><p>Plain text content</p></body></html>"
            result = extract_main_content(html, title="")  # no title → line 28 path
        assert "Plain text content" in result
        assert not result.startswith("#")

    def test_readability_no_display_title_returns_plain_text(self):
        """Hit line 20: readability succeeds but no display_title → return text directly."""
        mock_doc = MagicMock()
        mock_doc.summary.return_value = "<p>Content only</p>"
        mock_doc.title.return_value = ""  # empty title from readability
        with (
            patch(
                "omodul.knowledge.browser_extension.page_capture.Document", return_value=mock_doc
            ),
        ):
            result = extract_main_content("<html><body><p>Content only</p></body></html>", title="")
        # Should return plain text without # prefix
        assert "Content only" in result


# ── server: OSError in unlink (lines 108-109) ─────────────────────────────────


class TestRunIngestOsError:
    @pytest.mark.asyncio
    async def test_osunlink_failure_does_not_propagate(self):
        """Hit lines 108-109: OSError during tmp file cleanup is silently ignored."""
        from oskill.ingest_substrate import IngestResult

        mock_result = IngestResult(substrate_id="oserr_sub", medium="web_page")
        mock_ingest = AsyncMock(return_value=mock_result)

        with (
            patch("omodul.knowledge.browser_extension.server.ingest_substrate", mock_ingest),
            patch(
                "omodul.knowledge.browser_extension.server.os.unlink",
                side_effect=OSError("file busy"),
            ),
        ):
            substrate_id = await _run_ingest(
                "Title", "Content", "https://example.com", [], user_id_hash="test_user"
            )

        assert substrate_id == "oserr_sub"


# ── server: note creation failure is logged but ingest still succeeds ─────────


class TestIngestNoteCreationFailure:
    def test_note_creation_failure_does_not_fail_ingest(self, tmp_path, monkeypatch):
        """Hit lines 160-161: _create_note raises → logged, ingest still returns 200."""
        path = tmp_path / "token.txt"
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: path,
        )
        token = init_token()
        client = TestClient(app, raise_server_exceptions=False)

        mock_ingest = AsyncMock(return_value="note_fail_sub")
        mock_note = AsyncMock(side_effect=RuntimeError("DB full"))

        with (
            patch(
                "omodul.knowledge.browser_extension.server.check_url_existing", return_value=None
            ),
            patch("omodul.knowledge.browser_extension.server._run_ingest", mock_ingest),
            patch("omodul.knowledge.browser_extension.server.mark_url_ingested"),
            patch("omodul.knowledge.browser_extension.server._create_note", mock_note),
        ):
            resp = client.post(
                "/api/v1/browser-extension/ingest",
                headers={"X-Stratum-Token": token},
                json={
                    "url": "https://example.com/note-fail",
                    "title": "Article",
                    "html": "<p>Content</p>",
                    "create_note": True,
                    "note_content": "My note",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["substrate_id"] == "note_fail_sub"
        assert data["note_id"] is None  # note creation failed but ingest succeeded


# ── __main__.py: init subcommand ──────────────────────────────────────────────


class TestMainModule:
    def test_init_command_prints_token(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: tmp_path / "token.txt",
        )
        monkeypatch.setattr(sys, "argv", ["browser_extension", "init"])

        from omodul.knowledge.browser_extension.__main__ import main

        main()

        captured = capsys.readouterr()
        assert "Token generated" in captured.out
        assert (tmp_path / "token.txt").exists()

    def test_serve_command_starts_uvicorn(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["browser_extension"])
        # uvicorn is imported locally inside main(), patch at the module level
        with patch("uvicorn.run") as mock_run:
            from omodul.knowledge.browser_extension.__main__ import main

            main()
        mock_run.assert_called_once()
