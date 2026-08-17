"""Broker slots and workspace locks."""

from __future__ import annotations

import threading
import time

import pytest

from omodul.sandbox_broker import SandboxBroker


def test_slot_serializes_when_size_one() -> None:
    broker = SandboxBroker(slots={"process": 1})
    order: list[str] = []

    def worker(name: str) -> None:
        with broker.slot("process", timeout=5):
            order.append(f"{name}-in")
            time.sleep(0.05)
            order.append(f"{name}-out")

    threads = [
        threading.Thread(target=worker, args=("a",)),
        threading.Thread(target=worker, args=("b",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert order[0].endswith("-in")
    assert order[1].endswith("-out")
    assert len(order) == 4


def test_workspace_lock_is_per_path(tmp_path) -> None:
    broker = SandboxBroker()
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    held: list[str] = []
    with broker.workspace(str(a)):
        held.append("a")
        with broker.workspace(str(b)):
            held.append("b")
    assert held == ["a", "b"]


@pytest.mark.asyncio
async def test_async_workspace_lock_is_per_path(tmp_path) -> None:
    broker = SandboxBroker()
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    held: list[str] = []
    async with broker.async_workspace(str(a)):
        held.append("a")
        async with broker.async_workspace(str(b)):
            held.append("b")
    assert held == ["a", "b"]
