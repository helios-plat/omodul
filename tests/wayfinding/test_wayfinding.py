"""omodul.wayfinding 行为测试矩阵。

覆盖: map 生命周期/跨实例重建、frontier 计算 (阻塞边)、claim 冲突、
resolve→DecisionGist、rule_out_of_scope、fog add/graduate、
complete_if_clear、decisions_to_runbook 桥接、render_map_md、非法事件拒绝。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_THREE_O = Path(__file__).resolve().parents[3]
for _lib in ("obase", "oprim", "omodul", "oskill", "oservi"):
    _p = str(_THREE_O / _lib)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from obase.loop_event_store import AppendOnlyEventStore  # noqa: E402

from omodul.wayfinding import (  # noqa: E402
    MAP_ACTIVE,
    MAP_COMPLETED,
    TICKET_CLAIMED,
    TICKET_CLOSED,
    TICKET_OPEN,
    TICKET_OUT_OF_SCOPE,
    TICKET_RESEARCH,
    WayfindingKernel,
    WayfindingKernelError,
    decisions_to_runbook,
    render_map_md,
)


class TestMapLifecycleAndRebuild:
    async def test_create_map_sets_active_status(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m1.jsonl")
        kernel = WayfindingKernel(store, map_id="m1")
        m = await kernel.create_map("选下一代消息队列", notes="偏好开源")
        assert m.status == MAP_ACTIVE
        assert m.destination == "选下一代消息队列"

    async def test_duplicate_create_map_raises(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m1.jsonl")
        kernel = WayfindingKernel(store, map_id="m1")
        await kernel.create_map("d1")
        with pytest.raises(WayfindingKernelError):
            await kernel.create_map("d2")

    async def test_rebuild_from_fresh_instance_matches(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m1.jsonl")
        kernel = WayfindingKernel(store, map_id="m1")
        await kernel.create_map("d1", notes="n1")
        t = await kernel.add_ticket("Kafka or NATS?", "compare", TICKET_RESEARCH)
        await kernel.claim_ticket(t.id, "veya")

        fresh = WayfindingKernel(store, map_id="m1").rebuild()
        assert fresh.map.destination == "d1"
        assert fresh.map.tickets[t.id].status == TICKET_CLAIMED
        assert fresh.map.tickets[t.id].claimed_by == "veya"
        assert fresh.last_seq == kernel.last_seq

    async def test_apply_rejects_seq_gap(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m1.jsonl")
        kernel = WayfindingKernel(store, map_id="m1")
        await kernel.create_map("d1")
        bad_row = {"type": "ticket_added", "payload": {}, "seq": 99}
        with pytest.raises(WayfindingKernelError):
            kernel.apply(bad_row)

    def test_unknown_event_type_raises_on_replay(self, tmp_path):
        path = tmp_path / "m1.jsonl"
        store = AppendOnlyEventStore(path)
        kernel = WayfindingKernel(store, map_id="m1")

        async def _seed():
            await kernel.create_map("d1")

        import asyncio

        asyncio.run(_seed())
        # hand-craft a bogus follow-up event bypassing the kernel's own writers
        import asyncio as _a

        _a.run(store.append("telepathy_ping", {}))
        with pytest.raises(WayfindingKernelError):
            WayfindingKernel(store, map_id="m1").rebuild()


class TestFrontierAndBlocking:
    async def _seeded(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("d1")
        a = await kernel.add_ticket("A", "qa")
        b = await kernel.add_ticket("B", "qb")
        return kernel, a, b

    async def test_frontier_includes_all_open_unblocked_unclaimed(self, tmp_path):
        kernel, a, b = await self._seeded(tmp_path)
        assert {t.id for t in kernel.frontier()} == {a.id, b.id}

    async def test_blocked_ticket_excluded_until_blocker_closed(self, tmp_path):
        kernel, a, b = await self._seeded(tmp_path)
        await kernel.wire_blocking([(a.id, b.id)])
        assert {t.id for t in kernel.frontier()} == {a.id}
        await kernel.claim_ticket(a.id, "veya")
        await kernel.resolve_ticket(a.id, resolution="done", gist="A resolved")
        assert {t.id for t in kernel.frontier()} == {b.id}

    async def test_claimed_ticket_excluded_from_frontier(self, tmp_path):
        kernel, a, b = await self._seeded(tmp_path)
        await kernel.claim_ticket(a.id, "veya")
        assert {t.id for t in kernel.frontier()} == {b.id}

    async def test_wire_blocking_unknown_ticket_raises(self, tmp_path):
        kernel, a, b = await self._seeded(tmp_path)
        with pytest.raises(WayfindingKernelError):
            await kernel.wire_blocking([(a.id, "ghost")])


class TestClaimResolve:
    async def _one_ticket(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("d1")
        t = await kernel.add_ticket("Q", "question")
        return kernel, t

    async def test_claim_then_second_claim_fails(self, tmp_path):
        kernel, t = await self._one_ticket(tmp_path)
        r1 = await kernel.claim_ticket(t.id, "session-a")
        assert r1["ok"] is True
        r2 = await kernel.claim_ticket(t.id, "session-b")
        assert r2["ok"] is False
        assert r2["claimed_by"] == "session-a"

    async def test_claim_unknown_ticket_fails(self, tmp_path):
        kernel, _ = await self._one_ticket(tmp_path)
        r = await kernel.claim_ticket("ghost", "veya")
        assert r["ok"] is False

    async def test_resolve_without_claim_fails(self, tmp_path):
        kernel, t = await self._one_ticket(tmp_path)
        r = await kernel.resolve_ticket(t.id, resolution="x", gist="y")
        assert r["ok"] is False
        assert r["status"] == TICKET_OPEN

    async def test_resolve_after_claim_appends_decision_gist(self, tmp_path):
        kernel, t = await self._one_ticket(tmp_path)
        await kernel.claim_ticket(t.id, "veya")
        r = await kernel.resolve_ticket(t.id, resolution="did the work", gist="short answer")
        assert r["ok"] is True
        gists = kernel.decisions_so_far()
        assert len(gists) == 1
        assert gists[0].gist == "short answer"
        assert kernel.map.tickets[t.id].status == TICKET_CLOSED


class TestOutOfScope:
    async def test_rule_out_of_scope_closes_ticket(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("d1")
        t = await kernel.add_ticket("Q", "question")
        r = await kernel.rule_out_of_scope(t.id, "not this cycle")
        assert r["ok"] is True
        assert kernel.map.tickets[t.id].status == TICKET_OUT_OF_SCOPE
        assert t.id not in {f.id for f in kernel.frontier()}

    async def test_out_of_scope_ticket_never_returns_to_frontier(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("d1")
        t = await kernel.add_ticket("Q", "question")
        await kernel.rule_out_of_scope(t.id, "nope")
        assert kernel.frontier() == []

    async def test_double_rule_out_of_scope_fails(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("d1")
        t = await kernel.add_ticket("Q", "question")
        await kernel.rule_out_of_scope(t.id, "nope")
        r2 = await kernel.rule_out_of_scope(t.id, "nope again")
        assert r2["ok"] is False


class TestFog:
    async def test_add_fog_appears_in_not_yet_specified(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("d1")
        await kernel.add_fog("auth strategy unclear")
        assert kernel.map.not_yet_specified == ["auth strategy unclear"]

    async def test_graduate_fog_creates_tickets_and_clears_patch(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("d1")
        await kernel.add_fog("auth strategy unclear")
        created = await kernel.graduate_fog(
            "auth strategy unclear",
            [{"title": "OAuth vs session", "question": "which auth?", "type": TICKET_RESEARCH}],
        )
        assert len(created) == 1
        assert kernel.map.not_yet_specified == []
        assert created[0].id in kernel.map.tickets

    async def test_graduate_unknown_fog_patch_raises(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("d1")
        with pytest.raises(WayfindingKernelError):
            await kernel.graduate_fog("never added", [])


class TestCompleteIfClear:
    async def test_completes_when_frontier_and_fog_empty(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("d1")
        assert await kernel.complete_if_clear() is True
        assert kernel.map.status == MAP_COMPLETED
        assert kernel.is_complete() is True

    async def test_does_not_complete_with_open_frontier(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("d1")
        await kernel.add_ticket("Q", "question")
        assert await kernel.complete_if_clear() is False

    async def test_does_not_complete_with_fog_left(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("d1")
        await kernel.add_fog("something unclear")
        assert await kernel.complete_if_clear() is False


class TestDecisionsToRunbook:
    async def _cleared_map(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("ship feature X")
        t1 = await kernel.add_ticket("pick db", "which db?")
        await kernel.claim_ticket(t1.id, "veya")
        await kernel.resolve_ticket(t1.id, resolution="postgres", gist="use postgres")
        t2 = await kernel.add_ticket("pick queue", "which queue?")
        await kernel.claim_ticket(t2.id, "veya")
        await kernel.resolve_ticket(t2.id, resolution="nats", gist="use nats")
        return kernel

    async def test_no_decisions_raises(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("d1")
        with pytest.raises(WayfindingKernelError):
            decisions_to_runbook(kernel.map)

    async def test_compiles_sequential_runbook(self, tmp_path):
        kernel = await self._cleared_map(tmp_path)
        rb = decisions_to_runbook(kernel.map)
        assert len(rb.nodes) == 3  # 2 decisions + handoff
        assert "handoff" in rb.nodes
        assert rb.initial != "handoff"
        # linear chain: initial -> ... -> handoff
        froms = {e.from_node for e in rb.edges}
        tos = {e.to_node for e in rb.edges}
        assert "handoff" in tos
        assert rb.initial in froms

    async def test_runbook_is_executable_via_orchestrator(self, tmp_path):
        kernel = await self._cleared_map(tmp_path)
        rb = decisions_to_runbook(kernel.map)

        from obase.fs import FS
        from obase.orchestrator import start_runbook

        FS.set_default_working_dir(tmp_path / "obase_work")
        try:
            state = start_runbook(rb, run_id="bridge-run")
        finally:
            FS.reset_working_dir()
        assert state.current_node == rb.initial
        assert state.state == "running"


class TestRenderMapMd:
    async def test_includes_destination_and_decisions(self, tmp_path):
        store = AppendOnlyEventStore(tmp_path / "m.jsonl")
        kernel = WayfindingKernel(store, map_id="m")
        await kernel.create_map("ship feature X", notes="keep it small")
        t = await kernel.add_ticket("pick db", "which db?")
        await kernel.claim_ticket(t.id, "veya")
        await kernel.resolve_ticket(t.id, resolution="postgres", gist="use postgres")
        md = render_map_md(kernel.map)
        assert "ship feature X" in md
        assert "keep it small" in md
        assert "use postgres" in md
