"""omodul.sandbox_session — lifecycle around the oprim sandbox contract."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from oprim._sandbox_env import (
    sandbox_apply_patch,
    sandbox_create,
    sandbox_destroy,
    sandbox_exec,
    sandbox_get_file,
    sandbox_list,
    sandbox_put_file,
)
from oprim._sandbox_profile import sandbox_profile
from oskill._isolation_policy import isolation_policy

from omodul.sandbox_broker import get_broker


class SandboxSession:
    def __init__(
        self,
        *,
        ok: bool,
        sandbox_id: str = "",
        isolation: str = "",
        error: str = "",
        purpose: str = "",
        block_network: bool = False,
        note: str = "",
    ) -> None:
        self.ok = ok
        self.sandbox_id = sandbox_id
        self.isolation = isolation
        self.error = error
        self.purpose = purpose
        self.block_network = block_network
        self.note = note
        self._closed = False
        self._slot = ""
        self._workspace_key = ""
        self.owner_id = ""

    def exec(self, argv: list[str], **kwargs: Any) -> dict[str, Any]:
        if not self.ok:
            return {"ok": False, "error": self.error or "sandbox not open", "exit_code": -1}
        kwargs.setdefault("owner_id", self.owner_id)
        return sandbox_exec(self.sandbox_id, argv, **kwargs)

    def put_file(self, path: str, content: str | bytes) -> dict[str, Any]:
        if not self.ok:
            return {"ok": False, "error": self.error or "sandbox not open"}
        return sandbox_put_file(self.sandbox_id, path, content, owner_id=self.owner_id)

    def get_file(self, path: str) -> dict[str, Any]:
        if not self.ok:
            return {"ok": False, "error": self.error or "sandbox not open"}
        return sandbox_get_file(self.sandbox_id, path, owner_id=self.owner_id)

    def list(self, path: str = ".") -> dict[str, Any]:
        if not self.ok:
            return {"ok": False, "error": self.error or "sandbox not open"}
        return sandbox_list(self.sandbox_id, path, owner_id=self.owner_id)

    def apply_patch(self, patch: str) -> dict[str, Any]:
        if not self.ok:
            return {"ok": False, "error": self.error or "sandbox not open"}
        return sandbox_apply_patch(self.sandbox_id, patch, owner_id=self.owner_id)

    def close(self) -> None:
        if self._closed:
            return
        if self.sandbox_id:
            sandbox_destroy(self.sandbox_id, owner_id=self.owner_id)
        broker = get_broker()
        if self._slot:
            broker.release_slot(self._slot, owner_id=self.owner_id)
            self._slot = ""
        if self._workspace_key:
            broker.release_workspace(self._workspace_key)
            self._workspace_key = ""
        self._closed = True

    def __enter__(self) -> SandboxSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def sandbox_session(purpose: str = "chat_verify", **overrides: Any) -> SandboxSession:
    """Open a sandbox for ``purpose``. Failed create still returns a dead session."""
    profile = str(overrides.pop("profile", "") or sandbox_profile())
    owner_id = str(overrides.pop("owner_id", "") or "")
    policy = isolation_policy(purpose, profile=profile)
    if not policy.get("ok"):
        return SandboxSession(ok=False, error=str(policy.get("error") or ""), purpose=purpose)
    spec = {
        "isolation": overrides.get("isolation", policy["isolation"]),
        "image": overrides.get("image", policy["image"]),
        "block_network": overrides.get("block_network", policy["block_network"]),
        "cpu": overrides.get("cpu", policy["cpu"]),
        "memory": overrides.get("memory", policy["memory"]),
        "owner_id": owner_id,
    }
    if "workspace" in overrides:
        spec["workspace"] = overrides["workspace"]
    if "env" in overrides:
        spec["env"] = overrides["env"]
    broker = get_broker()
    session = SandboxSession(
        ok=False,
        isolation=str(spec["isolation"]),
        purpose=purpose,
        note=str(policy.get("note") or ""),
    )
    session.owner_id = owner_id
    if spec.get("workspace"):
        session._workspace_key = broker.acquire_workspace(str(spec["workspace"]))
    if not broker.acquire_slot(str(spec["isolation"]), timeout=60.0, owner_id=owner_id):
        who = f" for user {owner_id}" if owner_id else ""
        session.error = f"sandbox slot {spec['isolation']!r} busy{who}"
        session.close()
        return session
    session._slot = str(spec["isolation"])
    created = sandbox_create(**spec)
    if not created.get("ok"):
        session.error = str(created.get("error") or "create failed")
        session.close()
        return session
    session.ok = True
    session.sandbox_id = str(created["sandbox_id"])
    session.isolation = str(created["isolation"])
    session.block_network = bool(created.get("block_network"))
    return session


@contextmanager
def sandbox_scope(purpose: str = "chat_verify", **overrides: Any) -> Iterator[SandboxSession]:
    session = sandbox_session(purpose, **overrides)
    try:
        yield session
    finally:
        session.close()


def eval_in_sandbox(
    *,
    files: dict[str, str],
    test_args: list[str] | None = None,
    purpose: str = "pytest_eval",
    timeout_s: float = 60.0,
    owner_id: str = "",
    profile: str | None = None,
) -> dict[str, Any]:
    """Write ``files`` then run pytest. Never writes outside the sandbox."""
    extra: dict[str, Any] = {}
    if owner_id:
        extra["owner_id"] = owner_id
    if profile:
        extra["profile"] = profile
    with sandbox_scope(purpose, **extra) as session:
        if not session.ok:
            return {
                "ok": False,
                "passed": False,
                "error": session.error,
                "isolation": session.isolation,
                "purpose": purpose,
            }
        for rel, body in files.items():
            put = session.put_file(rel, body)
            if not put.get("ok"):
                return {
                    "ok": False,
                    "passed": False,
                    "error": put.get("error") or "put_file failed",
                    "isolation": session.isolation,
                }
        rec = session.exec(
            [sys.executable, "-m", "pytest", *(test_args or ["-q", "--tb=short"])],
            timeout_s=timeout_s,
        )
        rec["passed"] = bool(rec.get("ok"))
        rec["purpose"] = purpose
        rec["isolation"] = session.isolation
        return rec
