"""omodul.hitl_approval — generic human-in-the-loop approval gate.

3O layer: omodul (transaction orchestration primitive).
Suspends a calling coroutine until a human approves/rejects via
``resolve_approval`` (bridged from a UI route by the host), with a
mandatory timeout that auto-rejects. Used by oskill.zero_trust_vault
and available to any future high-risk omodul transaction.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

_log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300.0


class ApprovalTimeout(RuntimeError):
    """Raised when the human does not respond before the deadline."""


class ApprovalGate:
    """Per-task approval suspension: await human verdict or timeout."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self._events: dict[str, asyncio.Event] = {}
        self._results: dict[str, bool] = {}

    def request(self, timeout: float | None = None) -> str:
        """Register a new pending approval; returns the task_id to show in UI."""
        task_id = f"approval_{uuid.uuid4().hex[:12]}"
        self._events[task_id] = asyncio.Event()
        return task_id

    async def await_verdict(self, task_id: str, timeout: float | None = None) -> bool:
        """Suspend until resolve_approval() or timeout. True = approved."""
        event = self._events.get(task_id)
        if event is None:
            raise KeyError(f"unknown approval task {task_id}")
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout or self.timeout)
        except TimeoutError as exc:
            self._cleanup(task_id)
            raise ApprovalTimeout(
                f"审批超时(超过 {timeout or self.timeout:.0f}s 无人响应), 已自动拒绝"
            ) from exc
        approved = self._results.pop(task_id, False)
        self._cleanup(task_id)
        return approved

    def resolve(self, task_id: str, approved: bool) -> bool:
        """Human verdict delivered (from host route); wakes the coroutine."""
        event = self._events.get(task_id)
        if event is None:
            return False
        self._results[task_id] = approved
        event.set()
        return True

    def _cleanup(self, task_id: str) -> None:
        self._events.pop(task_id, None)
        self._results.pop(task_id, None)

    def pending(self) -> list[str]:
        return list(self._events)
