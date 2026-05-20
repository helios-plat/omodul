"""Tests for omodul.knowledge.browser_extension."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from omodul.knowledge.browser_extension.auth import init_token, get_token, verify_token, AuthError
from omodul.knowledge.browser_extension.page_capture import extract_main_content
from omodul.knowledge.browser_extension.url_dedup import normalize_url
from omodul.knowledge.browser_extension.server import app

# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def token(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "omodul.knowledge.browser_extension.auth._token_path",
        lambda: tmp_path / "browser_ext_token.txt",
    )
    return init_token()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ── Auth tests ────────────────────────────────────────────────────────────────

class TestAuth:
    def test_init_token_creates_file(self, tmp_path, monkeypatch):
        path = tmp_path / "token.txt"
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: path,
        )
        token = init_token()
        assert path.exists()
        assert len(token) >= 32
        assert path.read_text().strip() == token

    def test_get_token_returns_none_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: tmp_path / "nonexistent.txt",
        )
        assert get_token() is None

    @pytest.mark.asyncio
    async def test_verify_token_raises_if_not_configured(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: tmp_path / "nonexistent.txt",
        )
        with pytest.raises(AuthError, match="not configured"):
            await verify_token("anything")

    @pytest.mark.asyncio
    async def test_verify_token_raises_on_wrong_token(self, tmp_path, monkeypatch):
        path = tmp_path / "token.txt"
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: path,
        )
        init_token()
        with pytest.raises(AuthError, match="Invalid"):
            await verify_token("wrong_token")

    @pytest.mark.asyncio
    async def test_verify_token_passes_correct_token(self, tmp_path, monkeypatch):
        path = tmp_path / "token.txt"
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: path,
        )
        token = init_token()
        await verify_token(token)  # should not raise


# ── URL normalize tests ───────────────────────────────────────────────────────

class TestNormalizeUrl:
    def test_strips_utm_params(self):
        url = "https://example.com/post?utm_source=twitter&utm_medium=social"
        assert normalize_url(url) == "https://example.com/post"

    def test_strips_fbclid(self):
        url = "https://example.com/article?fbclid=abc123"
        assert normalize_url(url) == "https://example.com/article"

    def test_strips_fragment(self):
        url = "https://example.com/post#section1"
        assert normalize_url(url) == "https://example.com/post"

    def test_keeps_real_params(self):
        url = "https://example.com/search?q=python&page=2"
        result = normalize_url(url)
        assert "q=python" in result
        assert "page=2" in result

    def test_strips_utm_keeps_real(self):
        url = "https://example.com/post?utm_source=newsletter&id=42"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "id=42" in result

    def test_lowercases_scheme_and_host(self):
        url = "HTTPS://EXAMPLE.COM/path"
        result = normalize_url(url)
        assert result.startswith("https://example.com")


# ── Page capture tests ────────────────────────────────────────────────────────

class TestPageCapture:
    def test_extracts_text_from_html(self):
        html = "<html><body><h1>Hello</h1><p>World content here</p></body></html>"
        result = extract_main_content(html, title="Hello")
        assert "World content here" in result

    def test_handles_malformed_html(self):
        html = "<not valid html ><p>Some text</p>"
        result = extract_main_content(html)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_uses_title_when_provided(self):
        html = "<html><body><p>Content</p></body></html>"
        result = extract_main_content(html, title="My Title")
        assert "My Title" in result


# ── HTTP endpoint tests ───────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/v1/browser-extension/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestIngestEndpoint:
    def test_missing_token_returns_422(self, client):
        resp = client.post(
            "/api/v1/browser-extension/ingest",
            json={"url": "https://example.com", "title": "Test", "html": "<p>x</p>"},
        )
        assert resp.status_code == 422

    def test_invalid_token_returns_401(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: tmp_path / "token.txt",
        )
        init_token()
        resp = client.post(
            "/api/v1/browser-extension/ingest",
            headers={"X-Stratum-Token": "wrong_token"},
            json={"url": "https://example.com", "title": "Test", "html": "<p>x</p>"},
        )
        assert resp.status_code == 401

    def test_no_html_or_selection_returns_400(self, client, tmp_path, monkeypatch):
        path = tmp_path / "token.txt"
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: path,
        )
        token = init_token()
        resp = client.post(
            "/api/v1/browser-extension/ingest",
            headers={"X-Stratum-Token": token},
            json={"url": "https://example.com", "title": "Test"},
        )
        assert resp.status_code == 400

    def test_duplicate_url_returns_deduplicated(self, client, tmp_path, monkeypatch):
        path = tmp_path / "token.txt"
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: path,
        )
        token = init_token()

        with patch(
            "omodul.knowledge.browser_extension.server.check_url_existing",
            return_value="existing_substrate_id_123",
        ):
            resp = client.post(
                "/api/v1/browser-extension/ingest",
                headers={"X-Stratum-Token": token},
                json={
                    "url": "https://example.com/article",
                    "title": "Test",
                    "html": "<p>content</p>",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["deduplicated"] is True
        assert data["substrate_id"] == "existing_substrate_id_123"

    def test_new_ingest_calls_pipeline(self, client, tmp_path, monkeypatch):
        path = tmp_path / "token.txt"
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: path,
        )
        token = init_token()

        mock_ingest = AsyncMock(return_value="new_substrate_abc")
        with (
            patch("omodul.knowledge.browser_extension.server.check_url_existing", return_value=None),
            patch("omodul.knowledge.browser_extension.server._run_ingest", mock_ingest),
            patch("omodul.knowledge.browser_extension.server.mark_url_ingested"),
        ):
            resp = client.post(
                "/api/v1/browser-extension/ingest",
                headers={"X-Stratum-Token": token},
                json={
                    "url": "https://example.com/new",
                    "title": "New Article",
                    "html": "<html><body><p>New content</p></body></html>",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["deduplicated"] is False
        assert data["substrate_id"] == "new_substrate_abc"
        mock_ingest.assert_called_once()

    def test_selection_text_used_when_provided(self, client, tmp_path, monkeypatch):
        path = tmp_path / "token.txt"
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: path,
        )
        token = init_token()

        mock_ingest = AsyncMock(return_value="sel_substrate")
        with (
            patch("omodul.knowledge.browser_extension.server.check_url_existing", return_value=None),
            patch("omodul.knowledge.browser_extension.server._run_ingest", mock_ingest),
            patch("omodul.knowledge.browser_extension.server.mark_url_ingested"),
        ):
            resp = client.post(
                "/api/v1/browser-extension/ingest",
                headers={"X-Stratum-Token": token},
                json={
                    "url": "https://example.com/paper",
                    "title": "Paper",
                    "selection_text": "This is the selected excerpt from the paper.",
                },
            )

        assert resp.status_code == 200
        # Verify selection_text was passed as content (not html extraction)
        call_kwargs = mock_ingest.call_args
        assert "This is the selected excerpt" in call_kwargs.kwargs.get("content", "")


class TestSidebarSearchEndpoint:
    def test_invalid_token_returns_401(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: tmp_path / "token.txt",
        )
        init_token()
        resp = client.post(
            "/api/v1/browser-extension/sidebar-search",
            headers={"X-Stratum-Token": "wrong"},
            json={"url": "https://example.com", "page_title": "Hello"},
        )
        assert resp.status_code == 401

    def test_returns_results_list(self, client, tmp_path, monkeypatch):
        path = tmp_path / "token.txt"
        monkeypatch.setattr(
            "omodul.knowledge.browser_extension.auth._token_path",
            lambda: path,
        )
        token = init_token()

        from oskill.knowledge.hybrid_search import SearchResult
        mock_results = [
            SearchResult(
                type="substrate",
                id="sub_1",
                title="Related Paper",
                score=0.9,
                highlight="This is related content",
            )
        ]
        with patch(
            "omodul.knowledge.browser_extension.server.hybrid_search",
            AsyncMock(return_value=mock_results),
        ):
            resp = client.post(
                "/api/v1/browser-extension/sidebar-search",
                headers={"X-Stratum-Token": token},
                json={"url": "https://example.com", "page_title": "Related Paper"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["id"] == "sub_1"
        assert data["results"][0]["title"] == "Related Paper"
