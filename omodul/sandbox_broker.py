"""omodul.sandbox_broker — isolation slots + per-workspace locks.

Not a second Coordinator. Only queues sandbox/hicode access.
oservi may re-export this as the service facade.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

DEFAULT_SLOTS: dict[str, int] = {
    "process": 4,
    "netns": 4,
    "docker": 8,
    "opensandbox": 32,
    "memory": 16,
    # Hicode serve owns a live workspace/session; keep one process-wide slot
    # and use DEFAULT_PER_USER below for account isolation as well.
    "hicode_serve": 1,
}

DEFAULT_PER_USER: dict[str, int] = {
    "process": 2,
    "netns": 2,
    "docker": 2,
    "opensandbox": 2,
    "memory": 8,
    "hicode_serve": 1,
}


class SandboxBroker:
    def __init__(
        self,
        slots: dict[str, int] | None = None,
        per_user: dict[str, int] | None = None,
    ) -> None:
        self.slots = dict(DEFAULT_SLOTS if slots is None else slots)
        self.per_user = dict(DEFAULT_PER_USER if per_user is None else per_user)
        self._sem: dict[str, threading.Semaphore] = {
            key: threading.Semaphore(max(1, int(val))) for key, val in self.slots.items()
        }
        self._user_guard = threading.Lock()
        self._user_sem: dict[tuple[str, str], threading.Semaphore] = {}
        self._ws_guard = threading.Lock()
        self._ws: dict[str, threading.Lock] = {}
        self._async_sem: dict[str, asyncio.Semaphore] = {}
        self._async_user_sem: dict[tuple[str, str], asyncio.Semaphore] = {}
        self._async_ws_guard: asyncio.Lock | None = None
        self._async_ws: dict[str, asyncio.Lock] = {}

    def _sem_for(self, kind: str) -> threading.Semaphore:
        if kind not in self._sem:
            self._sem[kind] = threading.Semaphore(1)
            self.slots.setdefault(kind, 1)
        return self._sem[kind]

    def _user_sem_for(self, kind: str, owner_id: str) -> threading.Semaphore:
        cap = int(self.per_user.get(kind, self.slots.get(kind, 1)))
        key = (kind, owner_id)
        with self._user_guard:
            return self._user_sem.setdefault(key, threading.Semaphore(max(1, cap)))

    def acquire_slot(self, kind: str, timeout: float = 60.0, owner_id: str = "") -> bool:
        if not self._sem_for(kind).acquire(timeout=timeout):
            return False
        if owner_id:
            if not self._user_sem_for(kind, owner_id).acquire(timeout=timeout):
                self._sem_for(kind).release()
                return False
        return True

    def release_slot(self, kind: str, owner_id: str = "") -> None:
        if owner_id:
            self._user_sem_for(kind, owner_id).release()
        self._sem_for(kind).release()

    @contextmanager
    def slot(self, kind: str, timeout: float = 60.0, owner_id: str = "") -> Iterator[str]:
        if not self.acquire_slot(kind, timeout=timeout, owner_id=owner_id):
            who = f" for user {owner_id}" if owner_id else ""
            raise TimeoutError(f"sandbox slot {kind!r} busy{who}")
        try:
            yield kind
        finally:
            self.release_slot(kind, owner_id=owner_id)

    def _ws_key(self, workspace: str | None) -> str:
        if not workspace:
            return "_default"
        return str(Path(workspace).expanduser().resolve())

    def acquire_workspace(self, workspace: str | None) -> str:
        key = self._ws_key(workspace)
        with self._ws_guard:
            lock = self._ws.setdefault(key, threading.Lock())
        lock.acquire()
        return key

    def release_workspace(self, key: str) -> None:
        with self._ws_guard:
            lock = self._ws.get(key)
        if lock is not None:
            lock.release()

    @contextmanager
    def workspace(self, workspace: str | None) -> Iterator[str]:
        key = self.acquire_workspace(workspace)
        try:
            yield key
        finally:
            self.release_workspace(key)

    def _async_sem_for(self, kind: str) -> asyncio.Semaphore:
        if kind not in self._async_sem:
            self._async_sem[kind] = asyncio.Semaphore(max(1, int(self.slots.get(kind, 1))))
        return self._async_sem[kind]

    def _async_user_sem_for(self, kind: str, owner_id: str) -> asyncio.Semaphore:
        cap = int(self.per_user.get(kind, self.slots.get(kind, 1)))
        key = (kind, owner_id)
        if key not in self._async_user_sem:
            self._async_user_sem[key] = asyncio.Semaphore(max(1, cap))
        return self._async_user_sem[key]

    @asynccontextmanager
    async def async_slot(self, kind: str, owner_id: str = "") -> AsyncIterator[str]:
        sem = self._async_sem_for(kind)
        await sem.acquire()
        user_sem = self._async_user_sem_for(kind, owner_id) if owner_id else None
        if user_sem is not None:
            await user_sem.acquire()
        try:
            yield kind
        finally:
            if user_sem is not None:
                user_sem.release()
            sem.release()

    @asynccontextmanager
    async def async_workspace(self, workspace: str | None) -> AsyncIterator[str]:
        key = self._ws_key(workspace)
        if self._async_ws_guard is None:
            self._async_ws_guard = asyncio.Lock()
        async with self._async_ws_guard:
            lock = self._async_ws.setdefault(key, asyncio.Lock())
        await lock.acquire()
        try:
            yield key
        finally:
            lock.release()


_BROKER: SandboxBroker | None = None
_BROKER_GUARD = threading.Lock()


def get_broker() -> SandboxBroker:
    global _BROKER
    with _BROKER_GUARD:
        if _BROKER is None:
            _BROKER = SandboxBroker()
        return _BROKER


def set_broker(broker: SandboxBroker | None) -> None:
    global _BROKER
    with _BROKER_GUARD:
        _BROKER = broker


def reset_broker() -> None:
    set_broker(None)
