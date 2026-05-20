"""Push notifications for scheduled job outcomes."""
from __future__ import annotations

from oprim._logging import log


class Notifier:
    """Sends push notifications on job completion or failure."""

    def __init__(self, dispatcher=None) -> None:
        self._dispatcher = dispatcher

    async def notify_completion(self, job: dict, output: dict) -> None:
        if not self._dispatcher:
            return
        try:
            await self._dispatcher.push(
                user_id=job["user_id"],
                title=f"✓ {job['name']} completed",
                body=str(output)[:300],
            )
        except Exception as exc:
            log.warning("notifier_completion_failed", job=job["name"], error=str(exc))

    async def notify_failure(self, job: dict, error: str) -> None:
        if not self._dispatcher:
            return
        try:
            await self._dispatcher.push(
                user_id=job["user_id"],
                title=f"✗ {job['name']} failed",
                body=error[:300],
            )
        except Exception as exc:
            log.warning("notifier_failure_failed", job=job["name"], error=str(exc))
