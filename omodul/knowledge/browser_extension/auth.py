"""Token auth for browser extension API."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from oprim.errors import StratumError


class AuthError(StratumError):
    pass


def _token_path() -> Path:
    return Path.home() / ".stratum" / "secrets" / "browser_ext_token.txt"


def init_token() -> str:
    """Generate and persist a new token. Called once at first setup."""
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    os.chmod(path, 0o600)
    return token


def get_token() -> str | None:
    """Return the stored token, or None if not yet initialized."""
    path = _token_path()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return None


async def verify_token(token: str) -> None:
    """Raise AuthError if the token is wrong or not configured."""
    expected = get_token()
    if expected is None:
        raise AuthError("Token not configured. Run: python -m omodul.knowledge.browser_extension init")
    if not secrets.compare_digest(token, expected):
        raise AuthError("Invalid browser extension token")
