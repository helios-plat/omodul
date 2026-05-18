"""Tests for omodul.knowledge.start_mcp_server."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from omodul.knowledge.start_mcp_server import (
    _fetch_substrate_handler,
    _list_notes_handler,
    _pin_substrate_handler,
    _recent_changes_handler,
    _search_handler,
    _unpin_substrate_handler,
    create_stratum_mcp_server,
    start_mcp_server,
)


_MIGRATIONS = Path("/home/soffy/projects/platform/oprim/oprim/meta_db/migrations")


class TestCreateStratumMcpServer:
    def test_returns_fastmcp_instance(self):
        server = create_stratum_mcp_server()
        assert isinstance(server, FastMCP)

    def test_server_name_is_stratum(self):
        server = create_stratum_mcp_server()
        assert server.name == "stratum"

    def test_six_tools_registered(self):
        server = create_stratum_mcp_server()
        tools = server._tool_manager._tools
        assert len(tools) == 6

    def test_expected_tool_names_present(self):
        server = create_stratum_mcp_server()
        names = set(server._tool_manager._tools.keys())
        assert "stratum.search" in names
        assert "stratum.fetch_substrate" in names
        assert "stratum.list_notes" in names
        assert "stratum.recent_changes" in names
        assert "stratum.pin_substrate" in names
        assert "stratum.unpin_substrate" in names


class TestSearchHandler:
    async def test_returns_list(self, stratum_home):
        mock_result = MagicMock()
        mock_result.id = "sub001"
        mock_result.type = "substrate"
        mock_result.title = "Test"
        mock_result.score = 0.9
        mock_result.highlight = None
        mock_result.metadata = {"medium": "paper"}

        with patch("omodul.knowledge.start_mcp_server.hybrid_search",
                   new=AsyncMock(return_value=[mock_result])):
            results = await _search_handler("test query")

        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["id"] == "sub001"
        assert results[0]["score"] == 0.9

    async def test_empty_query_returns_empty(self, stratum_home):
        with patch("omodul.knowledge.start_mcp_server.hybrid_search",
                   new=AsyncMock(return_value=[])):
            results = await _search_handler("no match")
        assert results == []

    async def test_medium_filter_passed_through(self, stratum_home):
        with patch("omodul.knowledge.start_mcp_server.hybrid_search",
                   new=AsyncMock(return_value=[])) as mock_hs:
            await _search_handler("query", medium_filter=["paper"])
        mock_hs.assert_called_once_with("query", top_k=20, medium_filter=["paper"])


class TestFetchSubstrateHandler:
    def test_no_db_returns_error(self, stratum_home):
        result = _fetch_substrate_handler("NONEXISTENT")
        assert "error" in result

    def test_invalid_meta_json_falls_back(self, stratum_home):
        from oprim.meta_db import open_meta_db
        from oskill.knowledge._context import meta_db_path
        now = datetime.now(timezone.utc).isoformat()
        db = open_meta_db(meta_db_path())
        db.migrate(_MIGRATIONS)
        db.execute(
            "INSERT INTO substrate (id, ulid, title, mime, source_path, file_hash, byte_size, meta_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ["01ARZ3NDEKTSV4RRFFQ69G5FAW", "01ARZ3NDEKTSV4RRFFQ69G5FAW", "Bad JSON", "", "", "h002", 0, "NOT_JSON", now, now],
        )
        db.close()
        result = _fetch_substrate_handler("01ARZ3NDEKTSV4RRFFQ69G5FAW")
        assert result["id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAW"
        assert result["medium"] is None

    def test_unknown_id_returns_error(self, stratum_home):
        from oprim.meta_db import open_meta_db
        from oskill.knowledge._context import meta_db_path
        db = open_meta_db(meta_db_path())
        db.migrate(_MIGRATIONS)
        db.close()

        result = _fetch_substrate_handler("UNKNOWN_ID_000000000000000")
        assert "error" in result
        assert "not found" in result["error"]

    def test_known_id_returns_substrate(self, stratum_home):
        from oprim.meta_db import open_meta_db
        from oskill.knowledge._context import meta_db_path
        now = datetime.now(timezone.utc).isoformat()
        db = open_meta_db(meta_db_path())
        db.migrate(_MIGRATIONS)
        db.execute(
            "INSERT INTO substrate (id, ulid, title, mime, source_path, file_hash, byte_size, meta_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "01ARZ3NDEKTSV4RRFFQ69G5FAV", "Kelly Paper", "", "", "h001", 1024, '{"medium":"paper"}', now, now],
        )
        db.close()

        result = _fetch_substrate_handler("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        assert result["id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        assert result["title"] == "Kelly Paper"
        assert result["medium"] == "paper"
        assert result["byte_size"] == 1024


class TestListNotesHandler:
    def test_no_db_returns_empty(self, stratum_home):
        result = _list_notes_handler()
        assert result == []

    def test_returns_notes_list(self, stratum_home):
        from oprim.meta_db import open_meta_db
        from oskill.knowledge._context import meta_db_path
        now = datetime.now(timezone.utc).isoformat()
        db = open_meta_db(meta_db_path())
        db.migrate(_MIGRATIONS)
        db.execute(
            "INSERT INTO note (id, title, content, wikilinks, substrate_id, created_at) VALUES (?,?,?,?,?,?)",
            ["NOTE01", "My Note", "Content of the note", "[]", None, now],
        )
        db.close()

        result = _list_notes_handler(limit=10)
        assert len(result) == 1
        assert result[0]["id"] == "NOTE01"
        assert result[0]["title"] == "My Note"
        assert "Content of" in result[0]["content_preview"]

    def test_respects_limit(self, stratum_home):
        from oprim.meta_db import open_meta_db
        from oskill.knowledge._context import meta_db_path
        now = datetime.now(timezone.utc).isoformat()
        db = open_meta_db(meta_db_path())
        db.migrate(_MIGRATIONS)
        for i in range(5):
            db.execute(
                "INSERT INTO note (id, title, content, created_at) VALUES (?,?,?,?)",
                [f"NOTE0{i}", f"Note {i}", f"Content {i}", now],
            )
        db.close()

        result = _list_notes_handler(limit=3)
        assert len(result) == 3


class TestRecentChangesHandler:
    def test_no_db_returns_empty(self, stratum_home):
        result = _recent_changes_handler()
        assert result == []

    def test_returns_changefeed_events(self, stratum_home):
        from oprim.meta_db import open_meta_db
        from oskill.knowledge._context import meta_db_path
        db = open_meta_db(meta_db_path())
        db.migrate(_MIGRATIONS)
        db.execute(
            "INSERT INTO changefeed_local (seq, table_name, row_id, op, payload) VALUES (?,?,?,?,?)",
            [1, "substrate", "SUB001", "insert", '{"substrate_id":"SUB001"}'],
        )
        db.close()

        result = _recent_changes_handler(limit=10)
        assert len(result) == 1
        assert result[0]["seq"] == 1
        assert result[0]["table_name"] == "substrate"
        assert result[0]["op"] == "insert"

    def test_respects_limit(self, stratum_home):
        from oprim.meta_db import open_meta_db
        from oskill.knowledge._context import meta_db_path
        db = open_meta_db(meta_db_path())
        db.migrate(_MIGRATIONS)
        for i in range(1, 6):
            db.execute(
                "INSERT INTO changefeed_local (seq, table_name, row_id, op) VALUES (?,?,?,?)",
                [i, "substrate", f"SUB{i:03d}", "insert"],
            )
        db.close()

        result = _recent_changes_handler(limit=3)
        assert len(result) == 3
        assert result[0]["seq"] == 5  # newest first


class TestPinSubstrate:
    def test_no_db_returns_error(self, stratum_home):
        result = _pin_substrate_handler("SUBID001")
        assert "error" in result

    def test_pin_existing_substrate(self, stratum_home):
        from oprim.meta_db import open_meta_db
        from oskill.knowledge._context import meta_db_path
        now = datetime.now(timezone.utc).isoformat()
        db = open_meta_db(meta_db_path())
        db.migrate(_MIGRATIONS)
        db.execute(
            "INSERT INTO substrate (id, ulid, title, mime, source_path, file_hash, byte_size, meta_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ["PIN001", "PIN001", "Pinnable", "", "", "h001", 0, "{}", now, now],
        )
        db.close()

        result = _pin_substrate_handler("PIN001")
        assert result.get("is_pinned") is True
        assert result.get("substrate_id") == "PIN001"

        # Verify in DB
        db = open_meta_db(meta_db_path())
        rows = db.fetchall("SELECT is_pinned FROM substrate WHERE id = 'PIN001'")
        db.close()
        assert rows[0][0] is True

    def test_pin_nonexistent_substrate_returns_error(self, stratum_home):
        from oprim.meta_db import open_meta_db
        from oskill.knowledge._context import meta_db_path
        db = open_meta_db(meta_db_path())
        db.migrate(_MIGRATIONS)
        db.close()

        result = _pin_substrate_handler("DOES_NOT_EXIST_000000000000")
        assert "error" in result

    def test_unpin_substrate(self, stratum_home):
        from oprim.meta_db import open_meta_db
        from oskill.knowledge._context import meta_db_path
        now = datetime.now(timezone.utc).isoformat()
        db = open_meta_db(meta_db_path())
        db.migrate(_MIGRATIONS)
        db.execute(
            "INSERT INTO substrate (id, ulid, title, mime, source_path, file_hash, byte_size, meta_json, is_pinned, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ["UNPIN001", "UNPIN001", "Pinned", "", "", "h001", 0, "{}", True, now, now],
        )
        db.close()

        result = _unpin_substrate_handler("UNPIN001")
        assert result.get("is_pinned") is False

        db = open_meta_db(meta_db_path())
        rows = db.fetchall("SELECT is_pinned FROM substrate WHERE id = 'UNPIN001'")
        db.close()
        assert rows[0][0] is False


class TestStartMcpServer:
    def test_start_mcp_server_calls_run(self):
        with patch("omodul.knowledge.start_mcp_server.create_stratum_mcp_server") as mock_create:
            mock_server = MagicMock()
            mock_create.return_value = mock_server
            start_mcp_server(host="127.0.0.1", port=9999)
        mock_server.run.assert_called_once()
