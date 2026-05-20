"""Shared fixtures for views tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_views.duckdb"


@pytest.fixture(autouse=True)
def patch_meta_db_path(db_path: Path):
    """Redirect all views CRUD to a fresh per-test DuckDB."""
    with patch("omodul.knowledge.views.crud.meta_db_path", return_value=db_path):
        yield
