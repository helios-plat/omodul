"""omodul.knowledge.browser_extension — FastAPI server for Chrome/Firefox extension."""
from __future__ import annotations

from .auth import init_token, get_token
from .server import app

__all__ = ["app", "init_token", "get_token"]
