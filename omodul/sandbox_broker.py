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
    "docker": 2,
    "memory": 16,
    "hicode_serve": 1,
}


class SandboxBroker:
    def __init__(self, slots: dict[str, int] | None = None) -> None:
        self.slots = dict(DEFAULT_SLOTS if slots is None else slots)
        self._sem: dict[str, threading.Semaphore] = {
            key: threading.Semaphore(max(1, int(val))) for key, val in self.slots.items()
        }
        self._ws_guard = threading.Lock()
        self._ws: dict[str, threading.Lock] = {}
        self._async_sem: dict[str, asyncio.Semaphore] = {}
        self._async_ws_guard: asyncio.Lock | None = None
        self._async_ws: dict[str, asyncio.Lock] = {}

    def _sem_for(self, kind: str) -> threading.Semaphore:
        if kind not in self._sem:
            self._sem[kind] = threading.Semaphore(1)
            self.slots.setdefault(kind, 1)
        return self._sem[kind]

    def acquire_slot(self, kind: str, timeout: float = 60.0) -> bool:
        return bool(self._sem_for(kind).acquire(timeout=timeout))

    def release_slot(self, kind: str) -> None:
        self._sem_for(kind).release()

    @contextmanager
    def slot(self, kind: str, timeout: float = 60.0) -> Iterator[str]:
        if not self.acquire_slot(kind, timeout=timeout):
            raise TimeoutError(f"sandbox slot {kind!r} busy")
        try:
            yield kind
        finally:
            self.release_slot(kind)

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

    @asynccontextmanager
    async def async_slot(self, kind: str) -> AsyncIterator[str]:
        sem = self._async_sem_for(kind)
        await sem.acquire()
        try:
            yield kind
        finally:
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
