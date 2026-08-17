"""omodul.run_harness — exec a host/CLI harness inside a sandbox session."""

from __future__ import annotations

from typing import Any

from oskill._harness_argv import harness_argv

from omodul.sandbox_session import sandbox_scope


def run_harness(
    engine: str,
    prompt: str,
    *,
    workspace: str | None = None,
    purpose: str = "harness_host",
    timeout_s: float = 600.0,
    env: dict[str, str] | None = None,
    model: str | None = None,
    streaming: bool = False,
    extra: list[str] | None = None,
    bin: str | None = None,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    """Run one harness command. ``master`` is rejected. Caller workspace is not deleted."""
    if argv is None:
        built = harness_argv(
            engine, prompt, model=model, streaming=streaming, bin=bin, extra=extra
        )
        if not built.get("ok"):
            return built
        argv = list(built["argv"])
    overrides: dict[str, Any] = {}
    if workspace:
        overrides["workspace"] = workspace
    if env:
        overrides["env"] = env
    with sandbox_scope(purpose, **overrides) as session:
        if not session.ok:
            return {
                "ok": False,
                "engine": engine,
                "error": session.error,
                "isolation": session.isolation,
                "argv": argv,
            }
        rec = session.exec(argv, timeout_s=timeout_s, env=env)
        rec["engine"] = engine
        rec["argv"] = argv
        rec["output"] = rec.get("stdout") or ""
        return rec
