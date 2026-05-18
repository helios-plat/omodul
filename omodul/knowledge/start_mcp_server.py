"""Stratum MCP server — exposes 4 retrieval tools for Phase 1."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from oprim._logging import log
from oprim.mcp import create_mcp_server, register_tool

from oskill.knowledge._context import meta_db_path
from oskill.knowledge.hybrid_search import hybrid_search


# ── Tool handlers ────────────────────────────────────────────────────────────

async def _search_handler(
    query: str,
    top_k: int = 20,
    medium_filter: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Hybrid search across substrate (BM25 + dense vector, RRF fused)."""
    results = await hybrid_search(query, top_k=top_k, medium_filter=medium_filter)
    return [
        {
            "id": r.id,
            "type": r.type,
            "title": r.title,
            "score": r.score,
            "highlight": r.highlight,
            "metadata": r.metadata,
        }
        for r in results
    ]


def _fetch_substrate_handler(substrate_id: str) -> dict[str, Any]:
    """Fetch a single substrate record from meta_db by id."""
    db_p = meta_db_path()
    if not db_p.exists():
        return {"error": "meta_db not found"}
    try:
        from oprim.meta_db import open_meta_db
        db = open_meta_db(db_p)
        rows = db.fetchall(
            "SELECT id, ulid, title, mime, source_path, file_hash, byte_size, meta_json, created_at FROM substrate WHERE id = ?",
            [substrate_id],
        )
        db.close()
    except Exception as e:
        return {"error": str(e)}
    if not rows:
        return {"error": f"substrate {substrate_id!r} not found"}
    r = rows[0]
    try:
        meta = json.loads(r[7]) if r[7] else {}
    except Exception:
        meta = {}
    return {
        "id": r[0], "ulid": r[1], "title": r[2], "mime": r[3],
        "source_path": r[4], "file_hash": r[5], "byte_size": r[6],
        "medium": meta.get("medium"), "created_at": str(r[8]),
    }


def _list_notes_handler(limit: int = 20) -> list[dict[str, Any]]:
    """List notes from meta_db (most recent first)."""
    db_p = meta_db_path()
    if not db_p.exists():
        return []
    try:
        from oprim.meta_db import open_meta_db
        db = open_meta_db(db_p)
        rows = db.fetchall(
            "SELECT id, title, content, wikilinks, substrate_id, created_at FROM note ORDER BY created_at DESC LIMIT ?",
            [limit],
        )
        db.close()
    except Exception as e:
        log.warning("omodul.mcp.list_notes_error", error=str(e))
        return []
    return [
        {
            "id": r[0], "title": r[1],
            "content_preview": (r[2] or "")[:200],
            "wikilinks": r[3], "substrate_id": r[4],
            "created_at": str(r[5]),
        }
        for r in rows
    ]


def _recent_changes_handler(limit: int = 20) -> list[dict[str, Any]]:
    """List recent changefeed_local events (newest first)."""
    db_p = meta_db_path()
    if not db_p.exists():
        return []
    try:
        from oprim.meta_db import open_meta_db
        db = open_meta_db(db_p)
        rows = db.fetchall(
            "SELECT seq, table_name, row_id, op, payload, ts FROM changefeed_local ORDER BY seq DESC LIMIT ?",
            [limit],
        )
        db.close()
    except Exception as e:
        log.warning("omodul.mcp.recent_changes_error", error=str(e))
        return []
    return [
        {
            "seq": r[0], "table_name": r[1], "row_id": r[2],
            "op": r[3], "payload": r[4], "ts": str(r[5]),
        }
        for r in rows
    ]


# ── Server factory ────────────────────────────────────────────────────────────

def create_stratum_mcp_server() -> FastMCP:
    """Create and configure the Stratum MCP server (without starting it).

    Registers 4 Phase-1 tools:
      stratum.search, stratum.fetch_substrate, stratum.list_notes, stratum.recent_changes

    Not exposed (Phase 2+):
      stratum.fetch_content, stratum.fetch_concept, stratum.fetch_graph
    """
    server = create_mcp_server("stratum", version="0.1.0")

    register_tool(server, "stratum.search", _search_handler,
                  description="Hybrid BM25+vector search across Stratum substrate")
    register_tool(server, "stratum.fetch_substrate", _fetch_substrate_handler,
                  description="Fetch a substrate record by ID from meta_db")
    register_tool(server, "stratum.list_notes", _list_notes_handler,
                  description="List recent notes from Stratum meta_db")
    register_tool(server, "stratum.recent_changes", _recent_changes_handler,
                  description="List recent changefeed events from meta_db")

    log.info("omodul.mcp.server_created", tools=4)
    return server


def start_mcp_server(host: str = "0.0.0.0", port: int = 8765) -> None:
    """Create and run the Stratum MCP server (blocking)."""
    server = create_stratum_mcp_server()
    log.info("omodul.mcp.starting", host=host, port=port)
    server.run()


if __name__ == "__main__":  # pragma: no cover
    start_mcp_server()
