"""Additional coverage tests for browser_extension module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omodul.knowledge.browser_extension.auth import init_token
from omodul.knowledge.browser_extension.page_capture import extract_main_content
from omodul.knowledge.browser_extension.url_dedup import (
    normalize_url,
    check_url_existing,
    mark_url_ingested,
)
from omodul.knowledge.browser_extension.server import _run_ingest, _create_note, app
from fastapi.testclient import TestClient


# ── Page capture fallback paths ───────────────────────────────────────────────


class TestPageCaptureFallbacks:
    def test_readability_failure_falls_back_to_lxml(self, monkeypatch):
        """When readability raises, fall back to plain lxml text extraction."""
        with patch(
            "omodul.knowledge.browser_extension.page_capture.Document",
            MagicMock(side_effect=RuntimeError("readability unavailable")),
        ):
            html = "<html><body><p>Fallback text content</p></body></html>"
            result = extract_main_content(html, title="Fallback Test")
        assert "Fallback text content" in result

    def test_full_fallback_on_lxml_failure(self, monkeypatch):
        """When both readability and lxml fail, return title."""
        bad_lxml = MagicMock()
        bad_lxml.fromstring.side_effect = RuntimeError("lxml fail")
        with (
            patch(
                "omodul.knowledge.browser_extension.page_capture.Document",
                MagicMock(side_effect=RuntimeError("readability fail")),
            ),
            patch("omodul.knowledge.browser_extension.page_capture.lxml.html", bad_lxml),
        ):
            result = extract_main_content("<bad html>", title="Only Title")
        assert "Only Title" in result

    def test_empty_title_fallback(self, monkeypatch):
        """When both fail and no title given, returns 'Untitled'."""
        bad_lxml = MagicMock()
        bad_lxml.fromstring.side_effect = RuntimeError("lxml fail")
        with (
            patch(
                "omodul.knowledge.browser_extension.page_capture.Document",
                MagicMock(side_effect=RuntimeError("fail")),
            ),
            patch("omodul.knowledge.browser_extension.page_capture.lxml.html", bad_lxml),
        ):
            result = extract_main_content("<bad html>")
        assert result == "Untitled"


# ── URL dedup with mocked DB ───────────────────────────────────────────────────


class TestUrlDedupWithDb:
    def test_check_url_existing_returns_none_for_new_url(self, monkeypatch):
        mock_db = MagicMock()
        mock_db.fetchall.return_value = []
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.url_dedup._get_db",
            lambda: mock_db,
        )
        result = check_url_existing("https://example.com/new")
        assert result is None
        mock_db.fetchall.assert_called_once()

    def test_check_url_existing_returns_substrate_id(self, monkeypatch):
        mock_db = MagicMock()
        mock_db.fetchall.return_value = [("existing_sub_id",)]
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.url_dedup._get_db",
            lambda: mock_db,
        )
        result = check_url_existing("https://example.com/article")
        assert result == "existing_sub_id"

    def test_mark_url_ingested_calls_execute(self, monkeypatch):
        mock_db = MagicMock()
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.url_dedup._get_db",
            lambda: mock_db,
        )
        mark_url_ingested("https://example.com/article", "sub_abc123")
        mock_db.execute.assert_called_once()
        call_args = mock_db.execute.call_args
        assert "browser_ext_url_index" in call_args.args[0]
        assert "sub_abc123" in call_args.args[1]

    def test_mark_url_ingested_normalizes_url(self, monkeypatch):
        mock_db = MagicMock()
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.url_dedup._get_db",
            lambda: mock_db,
        )
        url = "https://example.com/page?utm_source=newsletter"
        mark_url_ingested(url, "sub_456")
        call_args = mock_db.execute.call_args
        params = call_args.args[1]
        # normalized_url should not contain utm_source
        assert "utm_source" not in params[2]


# ── _run_ingest function ──────────────────────────────────────────────────────


class TestRunIngest:
    @pytest.mark.asyncio
    async def test_run_ingest_writes_temp_file_and_calls_ingest(self):
        from oskill.ingest_substrate import IngestResult

        mock_result = IngestResult(substrate_id="ingest_sub_xyz", medium="web_page")
        mock_ingest = AsyncMock(return_value=mock_result)

        with patch(
            "omodul.knowledge.browser_extension.server.ingest_substrate",
            mock_ingest,
        ):
            substrate_id = await _run_ingest(
                title="Test Page",
                content="Hello world content",
                url="https://example.com",
                tags=["test"],
            )

        assert substrate_id == "ingest_sub_xyz"
        mock_ingest.assert_called_once()
        call_kwargs = mock_ingest.call_args
        assert call_kwargs.kwargs["source"]["type"] == "browser_extension"
        assert call_kwargs.kwargs["source"]["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_run_ingest_cleans_up_temp_file_on_success(self):
        from oskill.ingest_substrate import IngestResult

        captured_path: list[str] = []

        async def mock_ingest(path, source):
            captured_path.append(str(path))
            assert Path(str(path)).exists()
            return IngestResult(substrate_id="cleanup_test", medium="web_page")

        with patch("omodul.knowledge.browser_extension.server.ingest_substrate", mock_ingest):
            await _run_ingest("Title", "content", "https://example.com", [])

        # temp file should be cleaned up
        assert not Path(captured_path[0]).exists()

    @pytest.mark.asyncio
    async def test_run_ingest_cleans_up_temp_file_on_exception(self):
        captured_path: list[str] = []

        async def failing_ingest(path, source):
            captured_path.append(str(path))
            raise RuntimeError("ingest failed")

        with pytest.raises(RuntimeError, match="ingest failed"):
            with patch(
                "omodul.knowledge.browser_extension.server.ingest_substrate", failing_ingest
            ):
                await _run_ingest("Title", "content", "https://example.com", [])

        assert not Path(captured_path[0]).exists()


# ── _create_note function ─────────────────────────────────────────────────────


class TestCreateNote:
    @pytest.mark.asyncio
    async def test_create_note_inserts_into_db(self):
        mock_db = MagicMock()

        with (
            patch("omodul.knowledge.browser_extension.server.open_meta_db", return_value=mock_db),
            patch(
                "omodul.knowledge.browser_extension.server.meta_db_path",
                return_value=Path("/tmp/fake.duckdb"),
            ),
        ):
            note_id = await _create_note("sub_001", "Page Title", "Note content here")

        assert note_id is not None
        assert len(note_id) > 0
        mock_db.execute.assert_called_once()
        args = mock_db.execute.call_args.args
        assert "INSERT INTO note" in args[0]
        assert "Page Title" in args[1]
        assert "Note content here" in args[1]


# ── Ingest with note creation ─────────────────────────────────────────────────


class TestIngestWithNote:
    def test_ingest_with_note_returns_note_id(self, tmp_path, monkeypatch):
        path = tmp_path / "token.txt"
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: path,
        )
        token = init_token()
        client = TestClient(app, raise_server_exceptions=False)

        mock_ingest = AsyncMock(return_value="note_substrate")
        mock_note = AsyncMock(return_value="created_note_id")
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
                    "url": "https://example.com/with-note",
                    "title": "Article",
                    "html": "<p>Content</p>",
                    "create_note": True,
                    "note_content": "My annotation",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["note_id"] == "created_note_id"
        assert data["substrate_id"] == "note_substrate"


# ── Sidebar search with selected_text ────────────────────────────────────────


class TestSidebarWithSelectedText:
    def test_sidebar_includes_selected_text_in_query(self, tmp_path, monkeypatch):
        path = tmp_path / "token.txt"
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: path,
        )
        token = init_token()
        client = TestClient(app, raise_server_exceptions=False)

        from oskill.hybrid_search import SearchResult

        captured_queries: list[str] = []

        async def mock_search(query, **kwargs):
            captured_queries.append(query)
            return []

        with patch("omodul.knowledge.browser_extension.server.hybrid_search", mock_search):
            resp = client.post(
                "/api/v1/browser-extension/sidebar-search",
                headers={"X-Stratum-Token": token},
                json={
                    "url": "https://example.com",
                    "page_title": "Machine Learning",
                    "selected_text": "neural networks attention mechanism",
                },
            )

        assert resp.status_code == 200
        assert len(captured_queries) == 1
        assert "Machine Learning" in captured_queries[0]
        assert "neural networks attention mechanism" in captured_queries[0]
