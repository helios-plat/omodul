"""Stratum Browser Extension FastAPI server."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from oprim._logging import log
from oprim.errors import StratumError
from oprim.meta_db import open_meta_db
from oskill.knowledge._context import meta_db_path
from oskill.knowledge.hybrid_search import hybrid_search
from oskill.knowledge.ingest_substrate import ingest_substrate

from .auth import AuthError, verify_token
from .page_capture import extract_main_content
from .url_dedup import check_url_existing, mark_url_ingested

app = FastAPI(title="Stratum Browser Extension API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "chrome-extension://*",
        "moz-extension://*",
        "ms-browser-extension://*",
    ],
    allow_origin_regex=r"(chrome-extension|moz-extension|ms-browser-extension)://.*",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["X-Stratum-Token", "Content-Type"],
)


@app.exception_handler(AuthError)
async def _auth_error_handler(request: Request, exc: AuthError):
    return JSONResponse(status_code=401, content={"detail": str(exc)})


@app.exception_handler(StratumError)
async def _stratum_error_handler(request: Request, exc: StratumError):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


# ── Request / Response models ────────────────────────────────────────────────

class IngestRequest(BaseModel):
    url: str
    title: str
    html: Optional[str] = None
    selection_text: Optional[str] = None
    tags: list[str] = []
    create_note: bool = False
    note_content: Optional[str] = None


class IngestResponse(BaseModel):
    substrate_id: str
    note_id: Optional[str] = None
    deduplicated: bool
    message: str = ""


class SidebarSearchRequest(BaseModel):
    url: str
    page_title: str
    selected_text: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s-]+", "_", text).strip("_")[:60] or "webpage"


async def _run_ingest(title: str, content: str, url: str, tags: list[str]) -> str:
    """Write content to a temp HTML file, run ingest_substrate, return substrate_id."""
    slug = _slugify(title)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", prefix=f"{slug}_",
        delete=False, encoding="utf-8",
    ) as tf:
        tmp_path = tf.name
        tf.write(f"<html><head><title>{title}</title></head><body>{content}</body></html>")

    try:
        result = await ingest_substrate(
            path=Path(tmp_path),
            source={
                "type": "browser_extension",
                "url": url,
                "title": title,
                "tags": tags,
            },
        )
        return result.substrate_id
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/api/v1/browser-extension/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/api/v1/browser-extension/ingest", response_model=IngestResponse)
async def ingest_page(
    request: IngestRequest,
    x_stratum_token: str = Header(..., alias="X-Stratum-Token"),
):
    await verify_token(x_stratum_token)

    # URL dedup check
    existing_id = check_url_existing(request.url)
    if existing_id:
        log.info("browser_ext_url_duplicate", url=request.url, substrate_id=existing_id)
        return IngestResponse(
            substrate_id=existing_id,
            deduplicated=True,
            message=f"Already saved (substrate {existing_id})",
        )

    # Determine content
    if request.selection_text:
        content = request.selection_text
    elif request.html:
        content = extract_main_content(request.html, title=request.title)
    else:
        raise HTTPException(
            status_code=400,
            detail="Either html or selection_text is required",
        )

    substrate_id = await _run_ingest(
        title=request.title,
        content=content,
        url=request.url,
        tags=request.tags,
    )

    mark_url_ingested(request.url, substrate_id)

    note_id: str | None = None
    if request.create_note and request.note_content:
        try:
            note_id = await _create_note(substrate_id, request.title, request.note_content)
        except Exception as exc:
            log.warning("browser_ext_note_create_failed", error=str(exc))

    log.info(
        "browser_ext_ingest_ok",
        url=request.url,
        substrate_id=substrate_id,
        note_id=note_id,
    )
    return IngestResponse(
        substrate_id=substrate_id,
        note_id=note_id,
        deduplicated=False,
        message=f"Saved to Stratum",
    )


async def _create_note(substrate_id: str, title: str, content: str) -> str:
    """Create a note linked to a substrate."""
    import uuid
    import oprim.meta_db as _mod
    from datetime import datetime, timezone

    db_path = meta_db_path()
    db = open_meta_db(db_path)
    db.migrate(Path(_mod.__file__).parent / "migrations")
    note_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO note (id, title, content, wikilinks, substrate_id, meta_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, '{}', ?, ?)",
        [note_id, title, content, "[]", substrate_id, now, now],
    )
    return note_id


@app.post("/api/v1/browser-extension/sidebar-search")
async def sidebar_search(
    request: SidebarSearchRequest,
    x_stratum_token: str = Header(..., alias="X-Stratum-Token"),
):
    await verify_token(x_stratum_token)

    query = request.page_title
    if request.selected_text:
        query = f"{request.page_title} {request.selected_text}"

    results = await hybrid_search(
        query=query,
        top_k=10,
        mode="strict",
    )

    return {
        "results": [
            {
                "id": r.id,
                "type": r.type,
                "title": r.title,
                "score": r.score,
                "highlight": r.highlight,
            }
            for r in results
        ]
    }
