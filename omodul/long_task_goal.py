"""omodul.long_task_goal — 长程任务 GoalKernel 投影状态机 (事件溯源投影层)。

事件流 (``obase.loop_event_store.AppendOnlyEventStore``) 是真相源; GoalKernel
是投影 —— 从事件流重建 goal/todo/gate/evidence/handoff/quota 状态, 支持:

  * 跨重启恢复: 进程/机器重启后从事件流重建全部任务状态 (连续两天的根基);
  * 增量应用: 运行时每轮循环 apply 新事件, 无需全量重放;
  * 截断检测: 持有期望 ``last_seq`` (重建时的最大 seq), 与 ``store.verify()``
    的内部一致性校验互补 —— verify 保"无篡改/无空洞", 期望 seq 保"无尾部截断";
  * gate 状态机: operator gate 人工审批点 (等待 todo 完成 + 人工放行),
    auto gate 等待 todo 全 done 自动放行。

事件类型约定 (与 QuotaTracker 共用同一事件流):

  * ``goal_added``      payload: {goal_id, title, budget_usd?, meta?}
  * ``todo_updated``    payload: {todo_id, title?, status?, note?, blocked_by?}
  * ``gate_required``   payload: {gate_id, kind: operator|auto, waiting_on: [todo_id]}
  * ``gate_resolved``   payload: {gate_id, approved, by, note?}
  * ``evidence_appended`` payload: {todo_id, kind, detail, evidence_id}
  * ``handoff_recorded`` payload: {to, summary}
  * ``quota_*``         (由 obase.QuotaTracker 写入, 投影只读重建)

3O 元素: ``omodul.long_task_goal`` (``GoalKernel`` / ``Todo`` / ``Gate`` / ``Goal``)。
依赖: 仅 stdlib + ``obase.loop_event_store``。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from obase.loop_event_store import AppendOnlyEventStore, VerifyResult

# ---------------------------------------------------------------------------
# 事件类型常量
# ---------------------------------------------------------------------------

EVENT_GOAL_ADDED = "goal_added"
EVENT_TODO_UPDATED = "todo_updated"
EVENT_GATE_REQUIRED = "gate_required"
EVENT_GATE_RESOLVED = "gate_resolved"
EVENT_EVIDENCE_APPENDED = "evidence_appended"
EVENT_HANDOFF_RECORDED = "handoff_recorded"

# todo 状态机取值
TODO_OPEN = "open"
TODO_DONE = "done"
TODO_BLOCKED = "blocked"
TODO_DEFERRED = "deferred"
TODO_STATUSES = (TODO_OPEN, TODO_DONE, TODO_BLOCKED, TODO_DEFERRED)

# gate 状态
GATE_OPEN = "open"
GATE_RESOLVED = "resolved"
GATE_KIND_OPERATOR = "operator"
GATE_KIND_AUTO = "auto"

# goal 状态
GOAL_ACTIVE = "active"
GOAL_PAUSED = "paused"
GOAL_COMPLETED = "completed"


class GoalKernelError(Exception):
    """投影重建 / 状态机非法操作错误。"""


# ---------------------------------------------------------------------------
# 投影数据结构
# ---------------------------------------------------------------------------


@dataclass
class Todo:
    """可执行 todo (事件溯源投影)。"""

    id: str
    title: str
    status: str = TODO_OPEN
    note: str | None = None
    blocked_by: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    updated_at: float = 0.0


@dataclass
class Gate:
    """进度闸门: operator gate 人工审批, auto gate 自动放行。"""

    id: str
    kind: str = GATE_KIND_OPERATOR
    waiting_on: list[str] = field(default_factory=list)
    status: str = GATE_OPEN
    approved: bool | None = None
    resolved_at: float | None = None
    note: str | None = None


@dataclass
class Evidence:
    """证据日志条目 (与 todo 绑定)。"""

    id: str
    todo_id: str | None
    kind: str
    detail: Any
    ts: float


@dataclass
class Handoff:
    """可验证交接记录 (跨 agent/轮次)。"""

    to: str
    summary: str
    ts: float


@dataclass
class QuotaView:
    """goal 配额只读投影 (从 quota_* 事件重建, 与运行时 QuotaTracker 对齐)。"""

    budget_usd: float | None = None
    spent_usd: float = 0.0
    paused: bool = False

    @property
    def remaining_usd(self) -> float:
        if self.budget_usd is None:
            return float("inf")
        return max(0.0, self.budget_usd - self.spent_usd)


@dataclass
class Goal:
    """长程目标投影。"""

    id: str
    title: str
    status: str = GOAL_ACTIVE
    meta: dict[str, Any] = field(default_factory=dict)
    todos: dict[str, Todo] = field(default_factory=dict)
    gates: dict[str, Gate] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    handoffs: list[Handoff] = field(default_factory=list)
    quota: QuotaView = field(default_factory=QuotaView)

    def open_todos(self) -> list[Todo]:
        return [t for t in self.todos.values() if t.status == TODO_OPEN]

    def blocked_todos(self) -> list[Todo]:
        return [t for t in self.todos.values() if t.status == TODO_BLOCKED]

    def runnable_todos(self) -> list[Todo]:
        """解算 blocked_by: 返回当前可执行的 todo (依赖全 done 且自身未 done)。

        tracer-bullet 票的阻塞边由 blocked_by 声明; 某依赖完成后, 依赖它的
        todo 进入可运行集。返回顺序为创建顺序。
        """
        status = {t.id: t.status for t in self.todos.values()}
        return [
            t for t in self.todos.values()
            if t.status != TODO_DONE
            and all(status.get(dep) == TODO_DONE for dep in t.blocked_by)
        ]

    def next_action(self) -> Todo | None:
        """第一个可运行 todo (runnable_todos 优先, 回退 open_todos), 无则 None。"""
        return next(iter(self.runnable_todos()), self.next_open())

    def next_open(self) -> Todo | None:
        """第一个 open todo (按创建顺序), 无则 None。"""
        return next(iter(self.open_todos()), None)

    def pending_gates(self) -> list[Gate]:
        return [g for g in self.gates.values() if g.status == GATE_OPEN]

    def is_complete(self) -> bool:
        """全部 todo done 且无 open gate。"""
        return (not self.open_todos()) and (not self.pending_gates())


@dataclass
class IntegrityResult:
    """``GoalKernel.check_integrity()`` 结果。"""

    ok: bool
    verify: VerifyResult
    expected_last_seq: int | None
    actual_last_seq: int | None
    error: str | None = None


# ---------------------------------------------------------------------------
# GoalKernel
# ---------------------------------------------------------------------------


class GoalKernel:
    """goal 级投影状态机: 从事件流重建 + 增量应用 + 写 API。

    Usage::

        store = AppendOnlyEventStore(Path("~/.veya/loops/g1/events.jsonl"))
        kernel = GoalKernel(store, goal_id="g1")
        await kernel.add_goal("重构结算模块")
        await kernel.update_todo("t1", title="拆服务", status="done")
        await kernel.require_gate("operator", waiting_on=["t2"])
        kernel.rebuild()          # 跨天恢复: 从事件流重建
        kernel.next_action()      # 续跑决策

    写 API 先落事件 (store 锁内原子), 成功后再更新内存投影 —— 事件流与
    投影不会出现"内存先行"的分叉。
    """

    def __init__(self, store: AppendOnlyEventStore, *, goal_id: str) -> None:
        self._store = store
        self.goal_id = goal_id
        self.goal: Goal | None = None
        self.last_seq: int | None = None  # 重建时的期望基准 (截断检测用)

    # ------------------------------------------------------------------
    # 重建 / 增量
    # ------------------------------------------------------------------

    def rebuild(self) -> GoalKernel:
        """从事件流全量重建投影 (跨重启恢复入口)。校验失败抛 GoalKernelError。"""
        events = self._store.replay()  # 内部一致性校验, 损坏即抛
        self._project(events)
        return self

    def apply(self, row: dict[str, Any]) -> None:
        """增量应用单个事件 (运行时每轮循环调用)。"""
        if self.last_seq is not None and row["seq"] != self.last_seq + 1:
            raise GoalKernelError(f"event seq gap: expected {self.last_seq + 1}, got {row['seq']}")
        self._apply_one(row)
        self.last_seq = row["seq"]

    def check_integrity(self) -> IntegrityResult:
        """完整性校验: store 内部一致性 + 期望 last_seq 无尾部截断。

        verify 保"无篡改/无空洞"; 期望 seq 对比保"无尾部截断"
        (第①步测试明确的分工)。
        """
        verify = self._store.verify()
        if not verify.ok:
            return IntegrityResult(
                ok=False,
                verify=verify,
                expected_last_seq=self.last_seq,
                actual_last_seq=verify.last_seq,
                error=f"event stream corrupt: {verify.error}",
            )
        actual = verify.last_seq
        if self.last_seq is not None and actual != self.last_seq:
            return IntegrityResult(
                ok=False,
                verify=verify,
                expected_last_seq=self.last_seq,
                actual_last_seq=actual,
                error=(f"tail truncated: expected last_seq={self.last_seq}, actual={actual}"),
            )
        return IntegrityResult(
            ok=True,
            verify=verify,
            expected_last_seq=self.last_seq,
            actual_last_seq=actual,
        )

    # ------------------------------------------------------------------
    # 查询门面 (代理 Goal 投影, 便于 kernel 作为循环控制面直接使用)
    # ------------------------------------------------------------------

    def open_todos(self) -> list[Todo]:
        return self.goal.open_todos() if self.goal else []

    def blocked_todos(self) -> list[Todo]:
        return self.goal.blocked_todos() if self.goal else []

    def pending_gates(self) -> list[Gate]:
        return self.goal.pending_gates() if self.goal else []

    def next_action(self) -> Todo | None:
        """下一个可执行 todo (续跑决策)。"""
        return self.goal.next_action() if self.goal else None

    def is_complete(self) -> bool:
        return self.goal.is_complete() if self.goal else False

    # ------------------------------------------------------------------
    # 写 API (事件落流 + 投影同步)
    # ------------------------------------------------------------------

    async def add_goal(
        self,
        title: str,
        *,
        budget_usd: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        """创建长程目标。返回 goal_id。"""
        if self.goal is not None:
            raise GoalKernelError(f"goal {self.goal_id} already exists")
        row = await self._store.append(
            EVENT_GOAL_ADDED,
            {
                "goal_id": self.goal_id,
                "title": title,
                "budget_usd": budget_usd,
                "meta": meta or {},
            },
            dedupe_id=f"goal:{self.goal_id}",
        )
        self.apply(row)
        return self.goal_id

    async def update_todo(
        self,
        todo_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
        note: str | None = None,
        blocked_by: list[str] | None = None,
    ) -> Todo:
        """新建/更新 todo。status 非法抛 GoalKernelError。

        blocked_by: 前置 todo 依赖 (tracer-bullet 阻塞边); None 表示不改动。
        """
        if status is not None and status not in TODO_STATUSES:
            raise GoalKernelError(f"invalid todo status {status!r}; expected {TODO_STATUSES}")
        payload: dict[str, Any] = {"todo_id": todo_id}
        if title is not None:
            payload["title"] = title
        if status is not None:
            payload["status"] = status
        if note is not None:
            payload["note"] = note
        if blocked_by is not None:
            payload["blocked_by"] = list(blocked_by)
        row = await self._store.append(EVENT_TODO_UPDATED, payload)
        self.apply(row)
        await self._resolve_auto_gates()  # todo 置 done 可能满足 auto gate
        todo = self.goal.todos[todo_id] if self.goal else None
        if todo is None:
            raise GoalKernelError(f"todo {todo_id!r} missing after projection")
        return todo

    async def require_gate(
        self,
        kind: str,
        waiting_on: list[str],
        *,
        gate_id: str | None = None,
    ) -> Gate:
        """要求一个进度闸门 (operator 人工审批 / auto 自动放行)。"""
        if kind not in (GATE_KIND_OPERATOR, GATE_KIND_AUTO):
            raise GoalKernelError(f"invalid gate kind {kind!r}")
        gid = gate_id or f"gate-{time.time_ns()}"
        row = await self._store.append(
            EVENT_GATE_REQUIRED,
            {"gate_id": gid, "kind": kind, "waiting_on": list(waiting_on)},
        )
        self.apply(row)
        await self._resolve_auto_gates()  # 若 waiting_on 已全 done, 立即自动放行 (写事件)
        return self.goal.gates[gid]

    async def resolve_gate(
        self,
        gate_id: str,
        *,
        approved: bool,
        by: str,
        note: str | None = None,
    ) -> Gate:
        """人工放行/拒绝 gate。已 resolved 的 gate 不可重复放行。"""
        gate = self.goal.gates.get(gate_id) if self.goal else None
        if gate is None:
            raise GoalKernelError(f"gate {gate_id!r} not found")
        if gate.status == GATE_RESOLVED:
            raise GoalKernelError(f"gate {gate_id!r} already resolved")
        row = await self._store.append(
            EVENT_GATE_RESOLVED,
            {"gate_id": gate_id, "approved": approved, "by": by, "note": note},
        )
        self.apply(row)
        return self.goal.gates[gate_id]

    async def append_evidence(
        self,
        kind: str,
        detail: Any,
        *,
        todo_id: str | None = None,
    ) -> str:
        """追加证据日志 (可选绑定 todo)。返回 evidence_id。"""
        evidence_id = f"ev-{time.time_ns()}"
        row = await self._store.append(
            EVENT_EVIDENCE_APPENDED,
            {"evidence_id": evidence_id, "todo_id": todo_id, "kind": kind, "detail": detail},
        )
        self.apply(row)
        return evidence_id

    async def record_handoff(self, to: str, summary: str) -> None:
        """记录交接 (跨 agent/轮次), 事件流留痕。"""
        row = await self._store.append(
            EVENT_HANDOFF_RECORDED,
            {"to": to, "summary": summary},
        )
        self.apply(row)

    # ------------------------------------------------------------------
    # 投影引擎
    # ------------------------------------------------------------------

    def _project(self, events: list[dict[str, Any]]) -> None:
        self.goal = None
        self.last_seq = None
        for row in events:
            self._apply_one(row)
            self.last_seq = row["seq"]

    def _apply_one(self, row: dict[str, Any]) -> None:
        etype = row["type"]
        p = row.get("payload", {})
        if etype == EVENT_GOAL_ADDED:
            self._on_goal_added(p, row["ts"])
        elif etype == EVENT_TODO_UPDATED:
            self._require_goal()
            self._on_todo_updated(p, row["ts"])
        elif etype == EVENT_GATE_REQUIRED:
            self._require_goal()
            self._on_gate_required(p)
        elif etype == EVENT_GATE_RESOLVED:
            self._require_goal()
            self._on_gate_resolved(p, row["ts"])
        elif etype == EVENT_EVIDENCE_APPENDED:
            self._require_goal()
            self._on_evidence_appended(p, row["ts"])
        elif etype == EVENT_HANDOFF_RECORDED:
            self._require_goal()
            self._on_handoff_recorded(p, row["ts"])
        elif etype.startswith("quota_"):
            self._require_goal()
            self._on_quota_event(etype, p)
        else:
            raise GoalKernelError(f"unknown event type {etype!r}")

    def _require_goal(self) -> None:
        if self.goal is None:
            raise GoalKernelError(f"no goal yet: expected {EVENT_GOAL_ADDED} first")

    def _on_goal_added(self, p: dict[str, Any], ts: float) -> None:
        if self.goal is not None:
            raise GoalKernelError(f"duplicate goal event for {self.goal_id}")
        self.goal = Goal(
            id=p.get("goal_id", self.goal_id),
            title=p.get("title", ""),
            meta=dict(p.get("meta", {})),
        )
        budget = p.get("budget_usd")
        self.goal.quota = QuotaView(budget_usd=budget)

    def _on_todo_updated(self, p: dict[str, Any], ts: float) -> None:
        todo_id = p["todo_id"]
        todo = self.goal.todos.get(todo_id)
        if todo is None:
            todo = Todo(id=todo_id, title=p.get("title", todo_id))
            self.goal.todos[todo_id] = todo
        if "title" in p:
            todo.title = p["title"]
        if "status" in p:
            todo.status = p["status"]
        if "note" in p:
            todo.note = p["note"]
        if "blocked_by" in p:
            todo.blocked_by = list(p["blocked_by"])
        todo.updated_at = ts

    def _on_gate_required(self, p: dict[str, Any]) -> None:
        gid = p["gate_id"]
        self.goal.gates[gid] = Gate(
            id=gid,
            kind=p.get("kind", GATE_KIND_OPERATOR),
            waiting_on=list(p.get("waiting_on", [])),
        )

    def _on_gate_resolved(self, p: dict[str, Any], ts: float) -> None:
        gate = self.goal.gates.get(p["gate_id"])
        if gate is None:
            raise GoalKernelError(f"resolve unknown gate {p['gate_id']!r}")
        gate.status = GATE_RESOLVED
        gate.approved = bool(p.get("approved"))
        gate.resolved_at = ts
        gate.note = p.get("note")

    def _on_evidence_appended(self, p: dict[str, Any], ts: float) -> None:
        ev = Evidence(
            id=p["evidence_id"],
            todo_id=p.get("todo_id"),
            kind=p.get("kind", ""),
            detail=p.get("detail"),
            ts=ts,
        )
        self.goal.evidence.append(ev)
        if ev.todo_id and ev.todo_id in self.goal.todos:
            self.goal.todos[ev.todo_id].evidence_refs.append(ev.id)

    def _on_handoff_recorded(self, p: dict[str, Any], ts: float) -> None:
        self.goal.handoffs.append(Handoff(to=p.get("to", ""), summary=p.get("summary", ""), ts=ts))

    def _on_quota_event(self, etype: str, p: dict[str, Any]) -> None:
        q = self.goal.quota
        if etype == "quota_consumed":
            q.spent_usd = float(p.get("spent", q.spent_usd))
            if p.get("budget") is not None:
                q.budget_usd = float(p["budget"])
        elif etype == "quota_paused":
            q.paused = True
            if p.get("budget") is not None:
                q.budget_usd = float(p["budget"])
        elif etype == "quota_resumed":
            q.paused = False
            if p.get("budget") is not None:
                q.budget_usd = float(p["budget"])

    # ------------------------------------------------------------------
    # gate 自动放行
    # ------------------------------------------------------------------

    def _auto_resolvable_gates(self) -> list[str]:
        """返回 waiting_on 全 done 且仍 open 的 auto gate id。

        waiting_on 为空列表时视为"等待全部 todo"。auto 放行同样写事件流
        (``gate_resolved`` by="auto"), 保证投影与事件流一致。
        """
        if self.goal is None:
            return []
        resolvable: list[str] = []
        for gate in self.goal.pending_gates():
            if gate.kind != GATE_KIND_AUTO:
                continue
            waiting = gate.waiting_on or list(self.goal.todos)
            if all(
                self.goal.todos.get(t) and self.goal.todos[t].status == TODO_DONE for t in waiting
            ):
                resolvable.append(gate.id)
        return resolvable

    async def _resolve_auto_gates(self) -> None:
        """自动放行所有可放行的 auto gate (事件流 + 投影同步)。"""
        for gid in self._auto_resolvable_gates():
            row = await self._store.append(
                EVENT_GATE_RESOLVED,
                {"gate_id": gid, "approved": True, "by": "auto"},
            )
            self.apply(row)


__all__ = [
    "EVENT_GOAL_ADDED",
    "EVENT_TODO_UPDATED",
    "EVENT_GATE_REQUIRED",
    "EVENT_GATE_RESOLVED",
    "EVENT_EVIDENCE_APPENDED",
    "EVENT_HANDOFF_RECORDED",
    "TODO_OPEN",
    "TODO_DONE",
    "TODO_BLOCKED",
    "TODO_DEFERRED",
    "GATE_OPEN",
    "GATE_RESOLVED",
    "GATE_KIND_OPERATOR",
    "GATE_KIND_AUTO",
    "GOAL_ACTIVE",
    "GOAL_PAUSED",
    "GOAL_COMPLETED",
    "Evidence",
    "Gate",
    "Goal",
    "GoalKernel",
    "GoalKernelError",
    "Handoff",
    "IntegrityResult",
    "QuotaView",
    "Todo",
]
