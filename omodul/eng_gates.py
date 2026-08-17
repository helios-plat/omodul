"""omodul.eng_gates — orchestrate S1–S5. Coordinator sees only project_eng_gates."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

Profile = Literal["pre_merge", "hygiene", "gui"]
_PROFILES = {"pre_merge", "hygiene", "gui"}
_GUI_SUFFIXES = (
    ".html",
    ".css",
    ".scss",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".svelte",
    ".vue",
    ".qml",
)


def project_eng_gates(
    project_root: str,
    *,
    profile: str = "pre_merge",
    since_ref: str = "HEAD",
    gui_required: str | bool = "auto",
    force_full: bool = False,
    url: str = "",
    steps: list[dict[str, Any]] | None = None,
    request: str = "",
    skills: dict[str, Callable[..., dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Run one engineering-gate profile. Internal step order stays inside this module.

    Profiles:
      - pre_merge: S2 → S1 → (if gui_required) S5
      - hygiene:   S3 → S4
      - gui:       S5

    Never git-pushes. Never exposes S1–S5 as Coordinator-facing tools.
    """
    if profile not in _PROFILES:
        return {
            "ok": False,
            "profile": profile,
            "steps": [],
            "reason": f"unknown profile {profile!r}; expected one of {sorted(_PROFILES)}",
        }

    bag = _skill_bag(skills)

    ran: list[dict[str, Any]] = []
    if profile == "pre_merge":
        s2 = bag["s2"](project_root, since_ref=since_ref, force_full=force_full)
        ran.append(_step("pre_push_checks", s2))
        s1 = bag["s1"](project_root, since_ref=since_ref, files=s2.get("files"))
        ran.append(_step("code_review", s1))
        need_gui = _resolve_gui(gui_required, s2.get("files") or [], request)
        if need_gui:
            s5 = bag["s5"](project_root, url=url, steps=steps or [])
            ran.append(_step("record_browser_gif", s5))
    elif profile == "hygiene":
        s3 = bag["s3"](project_root, since_ref=since_ref)
        ran.append(_step("find_simplifications", s3))
        s4 = bag["s4"](project_root)
        ran.append(_step("archive_agent_notes", s4))
    else:
        s5 = bag["s5"](project_root, url=url, steps=steps or [])
        ran.append(_step("record_browser_gif", s5))

    ok = all(item["ok"] for item in ran) if ran else True
    return {
        "ok": ok,
        "profile": profile,
        "since_ref": since_ref,
        "gui_required": _resolve_gui(
            gui_required,
            next((s.get("result", {}).get("files") or [] for s in ran), []),
            request,
        )
        if profile == "pre_merge"
        else (profile == "gui" or gui_required is True),
        "force_full": force_full,
        "steps": ran,
        "reason": "" if ok else _first_reason(ran),
        "pushed": False,
    }


def _skill_bag(
    skills: dict[str, Callable[..., dict[str, Any]]] | None,
) -> dict[str, Callable[..., dict[str, Any]]]:
    overrides = skills or {}

    def _load(name: str, attr: str) -> Callable[..., dict[str, Any]]:
        if name in overrides:
            return overrides[name]
        mod = __import__(f"oskill.{attr}", fromlist=[attr])
        return getattr(mod, attr)

    return {
        "s1": _load("code_review", "code_review"),
        "s2": _load("pre_push_checks", "pre_push_checks"),
        "s3": _load("find_simplifications", "find_simplifications"),
        "s4": _load("archive_agent_notes", "archive_agent_notes"),
        "s5": _load("record_browser_gif", "record_browser_gif"),
    }


def _step(name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(result.get("ok")),
        "reason": result.get("reason") or result.get("verdict") or "",
        "result": result,
    }


def _resolve_gui(flag: str | bool, files: list[str], request: str) -> bool:
    if flag is True or flag == "true":
        return True
    if flag is False or flag == "false":
        return False
    blob = (request or "").lower()
    if any(tok in blob for tok in ("gui", "ui ", "前端", "页面", "browser", "playwright")):
        return True
    return any(str(f).lower().endswith(_GUI_SUFFIXES) for f in files)


def _first_reason(steps: list[dict[str, Any]]) -> str:
    for item in steps:
        if not item.get("ok"):
            return f"{item['name']}: {item.get('reason') or 'failed'}"
    return ""
