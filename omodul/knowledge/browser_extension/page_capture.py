"""Python-side HTML → plain text extraction (fallback when JS Readability not available)."""
from __future__ import annotations

import lxml.html
from readability import Document

from oprim._logging import log


def extract_main_content(html: str, title: str = "") -> str:
    """Extract main readable content from HTML using python-readability."""
    try:
        doc = Document(html)
        summary_html = doc.summary()
        # Prefer caller-supplied title; fall back to readability's extracted title
        display_title = title or doc.title() or ""
        text = lxml.html.fromstring(summary_html).text_content().strip()
        if display_title:
            return f"# {display_title}\n\n{text}"
        return text
    except Exception as exc:
        log.warning("page_capture_readability_failed", error=str(exc))
        # Fallback: strip all tags
        try:
            text = lxml.html.fromstring(html).text_content().strip()
            if title:
                return f"# {title}\n\n{text}"
            return text
        except Exception as exc2:
            log.error("page_capture_fallback_failed", error=str(exc2))
            return title or "Untitled"
