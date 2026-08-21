"""omodul.wayfinding — WayfindingKernel: 路径发现的事件溯源投影层。

事件流 (``obase.loop_event_store.AppendOnlyEventStore``) 是真相源;
WayfindingKernel 是投影 —— 与 ``omodul.long_task_goal.GoalKernel`` 同一套
模式 (事件溯源 + 增量 apply + 写 API async), 但解决的是不同问题:

  * GoalKernel: 路径已知, 追踪"谁在做/做没做/门开没开"(执行治理)。
  * WayfindingKernel: 路径未知, 用"认领一个模糊问题 → 写下决策"逐步把
    fog (``not_yet_specified``) 收敛成 ``decisions_so_far``, 直到
    frontier (open+unblocked+unclaimed 的 ticket) 清空。

收敛完成后用 ``decisions_to_runbook()`` 把 decisions 编译成
``obase.orchestrator.Runbook``, 交给已有的图状态机 (``start_runbook`` /
``runbook_goto``) 执行 —— Wayfinding 只负责"探路", 不负责"走路"。

事件类型约定::

  * ``map_created``       payload: {map_id, title, destination, notes}
  * ``ticket_added``      payload: {ticket_id, title, question, type}
  * ``blocking_wired``    payload: {edges: [[from_id, to_id], ...]}  (追加)
  * ``ticket_claimed``    payload: {ticket_id, claimed_by}
  * ``ticket_resolved``   payload: {ticket_id, resolution, gist, link, assets}
  * ``ticket_ruled_out``  payload: {ticket_id, reason}
  * ``fog_added``         payload: {patch}
  * ``fog_graduated``     payload: {patch, new_ticket_ids}
  * ``map_completed``     payload: {}

3O 元素: ``omodul.wayfinding`` (``WayfindingKernel`` / ``Map`` / ``Ticket`` /
``DecisionGist``)。依赖: ``obase.loop_event_store`` + ``obase.orchestrator``
(仅 ``decisions_to_runbook`` 用到)。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from obase.loop_event_store import AppendOnlyEventStore
from obase.orchestrator import Check, CheckType, Edge, Node, Runbook

# ---------------------------------------------------------------------------
# 事件类型常量
# ---------------------------------------------------------------------------

EVENT_MAP_CREATED = "map_created"
EVENT_TICKET_ADDED = "ticket_added"
EVENT_BLOCKING_WIRED = "blocking_wired"
EVENT_TICKET_CLAIMED = "ticket_claimed"
EVENT_TICKET_RESOLVED = "ticket_resolved"
EVENT_TICKET_RULED_OUT = "ticket_ruled_out"
EVENT_FOG_ADDED = "fog_added"
EVENT_FOG_GRADUATED = "fog_graduated"
EVENT_MAP_COMPLETED = "map_completed"

# ticket 类型
TICKET_RESEARCH = "research"  # AFK
TICKET_PROTOTYPE = "prototype"  # HITL
TICKET_GRILLING = "grilling"  # HITL
TICKET_TASK = "task"  # HITL or AFK
TICKET_TYPES = (TICKET_RESEARCH, TICKET_PROTOTYPE, TICKET_GRILLING, TICKET_TASK)

# ticket 状态
TICKET_OPEN = "open"
TICKET_CLAIMED = "claimed"
TICKET_CLOSED = "closed"
TICKET_OUT_OF_SCOPE = "out_of_scope"
TICKET_STATUSES = (TICKET_OPEN, TICKET_CLAIMED, TICKET_CLOSED, TICKET_OUT_OF_SCOPE)

# map 状态
MAP_ACTIVE = "active"
MAP_COMPLETED = "completed"
MAP_ABANDONED = "abandoned"


class WayfindingKernelError(Exception):
    """投影重建 / 状态机非法操作错误。"""


# ---------------------------------------------------------------------------
# 投影数据结构
# ---------------------------------------------------------------------------


@dataclass
class Ticket:
    id: str
    title: str
    question: str
    type: str = TICKET_TASK
    status: str = TICKET_OPEN
    claimed_by: str | None = None
    resolution: str | None = None
    gist: str | None = None
    assets: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    closed_at: float | None = None
    out_of_scope_reason: str | None = None


@dataclass
class DecisionGist:
    ticket_id: str
    title: str
    gist: str
    link: str


@dataclass
class Map:
    id: str
    title: str
    destination: str
    notes: str = ""
    status: str = MAP_ACTIVE
    tickets: dict[str, Ticket] = field(default_factory=dict)
    blocking: list[tuple[str, str]] = field(default_factory=list)
    decisions_so_far: list[DecisionGist] = field(default_factory=list)
    not_yet_specified: list[str] = field(default_factory=list)
    out_of_scope: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def open_tickets(self) -> list[Ticket]:
        return [t for t in self.tickets.values() if t.status == TICKET_OPEN]

    def frontier(self) -> list[Ticket]:
        """open + unblocked + unclaimed tickets, in creation order."""
        blockers_of: dict[str, list[str]] = {}
        for frm, to in self.blocking:
            blockers_of.setdefault(to, []).append(frm)
        result = []
        for t in self.open_tickets():
            if t.claimed_by:
                continue
            blockers = blockers_of.get(t.id, [])
            if any(
                self.tickets.get(b) is None or self.tickets[b].status != TICKET_CLOSED
                for b in blockers
            ):
                continue
            result.append(t)
        return result

    def is_clear(self) -> bool:
        """frontier empty and no unspecified fog left — ready to complete."""
        return not self.frontier() and not self.not_yet_specified

    def content_hash(self) -> str:
        payload = {
            "id": self.id,
            "destination": self.destination,
            "notes": self.notes,
            "decisions": [asdict(d) for d in self.decisions_so_far],
            "fog": self.not_yet_specified,
            "out_of_scope": self.out_of_scope,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def render_map_md(m: Map) -> str:
    """Regenerate the human-readable map.md view from structured Map state."""
    lines = [f"# {m.title}", "", "## Destination", "", m.destination, ""]
    if m.notes:
        lines += ["## Notes", "", m.notes, ""]
    lines += ["## Decisions so far", ""]
    for d in m.decisions_so_far:
        lines.append(f"- [{d.title}]({d.link}): {d.gist}")
    if not m.decisions_so_far:
        lines.append("(none yet)")
    lines += ["", "## Not yet specified", ""]
    for patch in m.not_yet_specified:
        lines.append(f"- {patch}")
    if not m.not_yet_specified:
        lines.append("(none)")
    lines += ["", "## Out of scope", ""]
    for item in m.out_of_scope:
        lines.append(f"- {item.get('title', item.get('ticket_id', ''))} — {item.get('reason', '')}")
    if not m.out_of_scope:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# WayfindingKernel
# ---------------------------------------------------------------------------


class WayfindingKernel:
    """map 级投影状态机: 从事件流重建 + 增量应用 + 写 API。

    Usage::

        map_id = new_map_id()
        kernel = WayfindingKernel(wayfinding_store(map_id), map_id=map_id)
        await kernel.create_map("选型下一代消息队列", notes="偏好开源, 单机可跑")
        t = await kernel.add_ticket("Kafka 还是 NATS?", "吞吐/运维成本对比", TICKET_RESEARCH)
        await kernel.claim_ticket(t.id, "veya")
        await kernel.resolve_ticket(t.id, resolution="...", gist="选 NATS, 运维成本低一个量级")

        fresh = WayfindingKernel(store, map_id=map_id).rebuild()  # 跨会话恢复

    并发说明: ``claim_ticket``/``resolve_ticket`` 在写前会 ``rebuild()`` 一次
    以缩小竞态窗口 (spec 要求"并发认领必须失败"), 但不是严格的跨进程 CAS ——
    两次 rebuild 之间仍有极短窗口可能双写。真正的强一致需要在
    ``AppendOnlyEventStore`` 上加 compare-and-append, 现状是"尽力窄化", 不是
    "杜绝"。
    """

    def __init__(self, store: AppendOnlyEventStore, *, map_id: str) -> None:
        self._store = store
        self.map_id = map_id
        self.map: Map | None = None
        self.last_seq: int | None = None

    # ------------------------------------------------------------------
    # 重建 / 增量
    # ------------------------------------------------------------------

    def rebuild(self) -> WayfindingKernel:
        events = self._store.replay()
        self._project(events)
        return self

    def apply(self, row: dict[str, Any]) -> None:
        if self.last_seq is not None and row["seq"] != self.last_seq + 1:
            raise WayfindingKernelError(
                f"event seq gap: expected {self.last_seq + 1}, got {row['seq']}"
            )
        self._apply_one(row)
        self.last_seq = row["seq"]

    # ------------------------------------------------------------------
    # 查询门面
    # ------------------------------------------------------------------

    def frontier(self) -> list[Ticket]:
        return self.map.frontier() if self.map else []

    def decisions_so_far(self) -> list[DecisionGist]:
        return list(self.map.decisions_so_far) if self.map else []

    def is_complete(self) -> bool:
        return self.map.status == MAP_COMPLETED if self.map else False

    # ------------------------------------------------------------------
    # 写 API (事件落流 + 投影同步)
    # ------------------------------------------------------------------

    async def create_map(
        self, destination: str, *, notes: str = "", title: str | None = None
    ) -> Map:
        if self.map is not None:
            raise WayfindingKernelError(f"map already created for {self.map_id}")
        row = await self._append(
            EVENT_MAP_CREATED,
            {
                "map_id": self.map_id,
                "title": title or f"Wayfind: {destination[:60]}",
                "destination": destination,
                "notes": notes,
            },
        )
        self.apply(row)
        assert self.map is not None
        return self.map

    async def add_ticket(self, title: str, question: str, ticket_type: str = TICKET_TASK) -> Ticket:
        self._require_map()
        if ticket_type not in TICKET_TYPES:
            raise WayfindingKernelError(f"unknown ticket type: {ticket_type!r}")
        ticket_id = uuid.uuid4().hex[:10]
        row = await self._append(
            EVENT_TICKET_ADDED,
            {"ticket_id": ticket_id, "title": title, "question": question, "type": ticket_type},
        )
        self.apply(row)
        return self.map.tickets[ticket_id]

    async def wire_blocking(self, edges: list[tuple[str, str]]) -> None:
        self._require_map()
        for frm, to in edges:
            if frm not in self.map.tickets or to not in self.map.tickets:
                raise WayfindingKernelError(f"blocking edge references unknown ticket: {frm}->{to}")
        row = await self._append(EVENT_BLOCKING_WIRED, {"edges": [[f, t] for f, t in edges]})
        self.apply(row)

    async def claim_ticket(self, ticket_id: str, claimed_by: str) -> dict[str, Any]:
        self._require_map()
        self.rebuild()  # narrow the concurrent-claim race window, see class docstring
        ticket = self.map.tickets.get(ticket_id)
        if ticket is None:
            return {"ok": False, "reason": f"ticket not found: {ticket_id}"}
        if ticket.status != TICKET_OPEN or ticket.claimed_by:
            return {
                "ok": False,
                "reason": "not claimable",
                "status": ticket.status,
                "claimed_by": ticket.claimed_by,
            }
        row = await self._append(
            EVENT_TICKET_CLAIMED, {"ticket_id": ticket_id, "claimed_by": claimed_by}
        )
        self.apply(row)
        return {"ok": True, "ticket_id": ticket_id, "claimed_by": claimed_by}

    async def resolve_ticket(
        self,
        ticket_id: str,
        *,
        resolution: str,
        gist: str,
        link: str | None = None,
        assets: list[str] | None = None,
    ) -> dict[str, Any]:
        self._require_map()
        self.rebuild()
        ticket = self.map.tickets.get(ticket_id)
        if ticket is None:
            return {"ok": False, "reason": f"ticket not found: {ticket_id}"}
        if ticket.status != TICKET_CLAIMED:
            return {"ok": False, "reason": "not claimed", "status": ticket.status}
        row = await self._append(
            EVENT_TICKET_RESOLVED,
            {
                "ticket_id": ticket_id,
                "resolution": resolution,
                "gist": gist,
                "link": link or ticket_id,
                "assets": assets or [],
            },
        )
        self.apply(row)
        return {"ok": True, "ticket_id": ticket_id}

    async def rule_out_of_scope(self, ticket_id: str, reason: str) -> dict[str, Any]:
        self._require_map()
        ticket = self.map.tickets.get(ticket_id)
        if ticket is None:
            return {"ok": False, "reason": f"ticket not found: {ticket_id}"}
        if ticket.status in (TICKET_CLOSED, TICKET_OUT_OF_SCOPE):
            return {"ok": False, "reason": "already closed", "status": ticket.status}
        row = await self._append(EVENT_TICKET_RULED_OUT, {"ticket_id": ticket_id, "reason": reason})
        self.apply(row)
        return {"ok": True, "ticket_id": ticket_id}

    async def add_fog(self, patch: str) -> None:
        self._require_map()
        row = await self._append(EVENT_FOG_ADDED, {"patch": patch})
        self.apply(row)

    async def graduate_fog(self, patch: str, new_tickets: list[dict[str, Any]]) -> list[Ticket]:
        """Move a fog patch into one or more sharp tickets, clearing it from the fog list."""
        self._require_map()
        if patch not in self.map.not_yet_specified:
            raise WayfindingKernelError(f"fog patch not found: {patch!r}")
        created = [
            await self.add_ticket(spec["title"], spec["question"], spec.get("type", TICKET_TASK))
            for spec in new_tickets
        ]
        row = await self._append(
            EVENT_FOG_GRADUATED, {"patch": patch, "new_ticket_ids": [t.id for t in created]}
        )
        self.apply(row)
        return created

    async def complete_if_clear(self) -> bool:
        self._require_map()
        if self.map.status != MAP_ACTIVE or not self.map.is_clear():
            return False
        row = await self._append(EVENT_MAP_COMPLETED, {})
        self.apply(row)
        return True

    # ------------------------------------------------------------------
    # internal — event append helper
    # ------------------------------------------------------------------

    async def _append(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._store.append(event_type, payload)

    # ------------------------------------------------------------------
    # internal — projection
    # ------------------------------------------------------------------

    def _project(self, events: list[dict[str, Any]]) -> None:
        self.map = None
        self.last_seq = None
        for row in events:
            self._apply_one(row)
            self.last_seq = row["seq"]

    def _apply_one(self, row: dict[str, Any]) -> None:
        etype = row["type"]
        p = row.get("payload", {})
        ts = row.get("ts", time.time())
        if etype == EVENT_MAP_CREATED:
            self._on_map_created(p, ts)
        elif etype == EVENT_TICKET_ADDED:
            self._require_map()
            self._on_ticket_added(p, ts)
        elif etype == EVENT_BLOCKING_WIRED:
            self._require_map()
            self._on_blocking_wired(p)
        elif etype == EVENT_TICKET_CLAIMED:
            self._require_map()
            self._on_ticket_claimed(p, ts)
        elif etype == EVENT_TICKET_RESOLVED:
            self._require_map()
            self._on_ticket_resolved(p, ts)
        elif etype == EVENT_TICKET_RULED_OUT:
            self._require_map()
            self._on_ticket_ruled_out(p, ts)
        elif etype == EVENT_FOG_ADDED:
            self._require_map()
            self._on_fog_added(p)
        elif etype == EVENT_FOG_GRADUATED:
            self._require_map()
            self._on_fog_graduated(p)
        elif etype == EVENT_MAP_COMPLETED:
            self._require_map()
            self._on_map_completed(p, ts)
        else:
            raise WayfindingKernelError(f"unknown event type {etype!r}")

    def _require_map(self) -> None:
        if self.map is None:
            raise WayfindingKernelError(f"no map yet: expected {EVENT_MAP_CREATED} first")

    def _on_map_created(self, p: dict[str, Any], ts: float) -> None:
        if self.map is not None:
            raise WayfindingKernelError(f"duplicate map event for {self.map_id}")
        self.map = Map(
            id=p.get("map_id", self.map_id),
            title=p.get("title", ""),
            destination=p.get("destination", ""),
            notes=p.get("notes", ""),
            created_at=ts,
            updated_at=ts,
        )

    def _on_ticket_added(self, p: dict[str, Any], ts: float) -> None:
        tid = p["ticket_id"]
        self.map.tickets[tid] = Ticket(
            id=tid,
            title=p.get("title", tid),
            question=p.get("question", ""),
            type=p.get("type", TICKET_TASK),
            created_at=ts,
            updated_at=ts,
        )
        self.map.updated_at = ts

    def _on_blocking_wired(self, p: dict[str, Any]) -> None:
        for frm, to in p.get("edges", []):
            edge = (frm, to)
            if edge not in self.map.blocking:
                self.map.blocking.append(edge)

    def _on_ticket_claimed(self, p: dict[str, Any], ts: float) -> None:
        ticket = self.map.tickets[p["ticket_id"]]
        ticket.status = TICKET_CLAIMED
        ticket.claimed_by = p["claimed_by"]
        ticket.updated_at = ts
        self.map.updated_at = ts

    def _on_ticket_resolved(self, p: dict[str, Any], ts: float) -> None:
        ticket = self.map.tickets[p["ticket_id"]]
        ticket.status = TICKET_CLOSED
        ticket.resolution = p.get("resolution")
        ticket.gist = p.get("gist")
        ticket.assets = list(p.get("assets", []))
        ticket.updated_at = ts
        ticket.closed_at = ts
        self.map.decisions_so_far.append(
            DecisionGist(
                ticket_id=ticket.id,
                title=ticket.title,
                gist=p.get("gist", ""),
                link=p.get("link", ticket.id),
            )
        )
        self.map.updated_at = ts

    def _on_ticket_ruled_out(self, p: dict[str, Any], ts: float) -> None:
        ticket = self.map.tickets[p["ticket_id"]]
        ticket.status = TICKET_OUT_OF_SCOPE
        ticket.out_of_scope_reason = p.get("reason")
        ticket.updated_at = ts
        ticket.closed_at = ts
        self.map.out_of_scope.append(
            {"ticket_id": ticket.id, "title": ticket.title, "reason": p.get("reason", "")}
        )
        self.map.updated_at = ts

    def _on_fog_added(self, p: dict[str, Any]) -> None:
        patch = p["patch"]
        if patch not in self.map.not_yet_specified:
            self.map.not_yet_specified.append(patch)

    def _on_fog_graduated(self, p: dict[str, Any]) -> None:
        patch = p["patch"]
        self.map.not_yet_specified = [f for f in self.map.not_yet_specified if f != patch]

    def _on_map_completed(self, p: dict[str, Any], ts: float) -> None:
        self.map.status = MAP_COMPLETED
        self.map.updated_at = ts


# ---------------------------------------------------------------------------
# store factory
# ---------------------------------------------------------------------------


def new_map_id() -> str:
    return uuid.uuid4().hex[:12]


def wayfinding_store(map_id: str, root: Path | str | None = None) -> AppendOnlyEventStore:
    base = Path(
        root or os.environ.get("THREE_O_WAYFINDING_DIR", Path.home() / ".local/state/3o/wayfinding")
    )
    return AppendOnlyEventStore(base / map_id / "events.jsonl")


def load_kernel(map_id: str, root: Path | str | None = None) -> WayfindingKernel:
    return WayfindingKernel(wayfinding_store(map_id, root), map_id=map_id).rebuild()


# ---------------------------------------------------------------------------
# Bridge: Decisions -> Runbook
# ---------------------------------------------------------------------------


def decisions_to_runbook(m: Map, name: str | None = None) -> Runbook:
    """Compile a map's closed decisions into a sequential, checked Runbook.

    v0.1 strategy (matches 3O Wayfinding spec section 6): one node per
    decision in resolution order, a checklist check gating each transition,
    then a handoff node. Richer mapping (parallel branches from the blocking
    graph, ticket-type-aware node shape) is left for a later iteration —
    this is enough to hand a cleared map to obase.orchestrator.start_runbook.
    """
    if not m.decisions_so_far:
        raise WayfindingKernelError("no decisions to compile")

    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    prev: str | None = None
    for i, d in enumerate(m.decisions_so_far):
        nid = f"d{i}_{d.ticket_id[:8]}"
        nodes[nid] = Node(
            id=nid,
            prompt=f"Execute decision: {d.title}\n\nGist: {d.gist}\n\nSee: {d.link}",
            before_transfer=[
                Check(
                    type=CheckType.CHECKLIST,
                    payload={"items": [f"Decision '{d.title}' applied / verified"]},
                )
            ],
        )
        if prev is not None:
            edges.append(Edge(from_node=prev, to_node=nid, condition=f"Completed {prev}"))
        prev = nid

    handoff = "handoff"
    nodes[handoff] = Node(
        id=handoff, prompt="Summarize execution of all decisions and remaining risks."
    )
    if prev is not None:
        edges.append(Edge(from_node=prev, to_node=handoff, condition="All decisions executed"))

    return Runbook(
        name=name or f"from-map-{m.id}",
        initial=next(iter(nodes)),
        nodes=nodes,
        edges=edges,
        version="1",
    )
