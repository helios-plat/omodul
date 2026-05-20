"""Preset view loader — installs the 5 builtin views (idempotent)."""
from __future__ import annotations

from pathlib import Path

import yaml

from .crud import create_view, list_views

PRESETS_DIR = Path(__file__).parent / "presets"


def install_builtin_views(user_id: str) -> list[dict]:
    """Install the 5 preset views for *user_id* (idempotent).

    Returns a list of newly created view dicts (empty if all already exist).
    """
    existing_names = {v["name"] for v in list_views(user_id)}
    installed: list[dict] = []

    for preset_file in sorted(PRESETS_DIR.glob("*.yaml")):
        spec = yaml.safe_load(preset_file.read_text(encoding="utf-8"))
        if spec["name"] in existing_names:
            continue
        spec["is_builtin"] = True
        view = create_view(user_id, spec)
        installed.append(view)

    return installed
