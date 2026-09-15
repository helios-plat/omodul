"""Deterministic projection of externally accepted progress.

This element consumes event and verdict *facts*.  It never interprets a
worker result, tool exit code, or model statement as acceptance.  Only an
explicit ``AcceptanceVerdict(status="PASS")`` creates an ``AcceptedUnit``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from obase.element_contract import ElementContract, zero_authority

PASS_STATUSES = frozenset({"PASS", "PASSED", "ACCEPTED"})
FAIL_STATUSES = frozenset({"FAIL", "FAILED", "REJECT", "REJECTED"})


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("expected a mapping")
    return value


@dataclass(frozen=True, slots=True)
class GoalRunEvent:
    event_id: str
    unit_id: str
    kind: str
    attempt: int = 1
    timestamp: float = 0.0
    payload: Mapping[str, Any] = field(default_factory=dict)
    sequence: int | None = None

    @property
    def event_type(self) -> str:
        return self.kind

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GoalRunEvent:
        payload = dict(data.get("payload", {}))
        unit_id = str(data.get("unit_id", payload.get("unit_id", "")))
        return cls(
            event_id=str(data.get("event_id", data.get("id", ""))),
            unit_id=unit_id,
            kind=str(data.get("kind", data.get("type", data.get("event_type", "unknown")))),
            attempt=int(data.get("attempt", payload.get("attempt", 1))),
            timestamp=float(data.get("timestamp", data.get("ts", 0.0))),
            payload=payload,
            sequence=(
                int(data["sequence"])
                if data.get("sequence") is not None
                else (int(data["seq"]) if data.get("seq") is not None else None)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "unit_id": self.unit_id,
            "kind": self.kind,
            "attempt": self.attempt,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
            "sequence": self.sequence,
        }


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    kind: str = "unknown"
    reference: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.evidence_id

    @classmethod
    def from_value(cls, value: Any) -> EvidenceRef:
        if isinstance(value, EvidenceRef):
            return value
        if isinstance(value, Mapping):
            return cls(
                evidence_id=str(value.get("evidence_id", value.get("id", ""))),
                kind=str(value.get("kind", "unknown")),
                reference=str(value.get("reference", value.get("uri", ""))),
                metadata=dict(value.get("metadata", {})),
            )
        return cls(str(value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "reference": self.reference,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class AcceptanceVerdict:
    verdict_id: str
    unit_id: str
    status: str
    revision: int = 1
    attempt: int = 1
    evidence_refs: tuple[EvidenceRef, ...] = ()
    reason: str = ""
    timestamp: float = 0.0

    @property
    def passed(self) -> bool:
        return self.status.upper() in PASS_STATUSES

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AcceptanceVerdict:
        return cls(
            verdict_id=str(data.get("verdict_id", data.get("id", ""))),
            unit_id=str(data.get("unit_id", "")),
            status=str(data.get("status", data.get("verdict", "PENDING"))).upper(),
            revision=int(data.get("revision", 1)),
            attempt=int(data.get("attempt", 1)),
            evidence_refs=tuple(
                EvidenceRef.from_value(item) for item in data.get("evidence_refs", [])
            ),
            reason=str(data.get("reason", "")),
            timestamp=float(data.get("timestamp", data.get("ts", 0.0))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict_id": self.verdict_id,
            "unit_id": self.unit_id,
            "status": self.status,
            "revision": self.revision,
            "attempt": self.attempt,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class CheckpointRef:
    checkpoint_id: str
    unit_id: str
    revision: str = ""
    reference: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "unit_id": self.unit_id,
            "revision": self.revision,
            "reference": self.reference,
        }


@dataclass(frozen=True, slots=True)
class FailureEvent:
    event_id: str
    unit_id: str
    attempt: int = 1
    reason: str = ""
    timestamp: float = 0.0

    def to_event(self) -> GoalRunEvent:
        return GoalRunEvent(
            event_id=self.event_id,
            unit_id=self.unit_id,
            kind="attempt_failed",
            attempt=self.attempt,
            timestamp=self.timestamp,
            payload={"reason": self.reason},
        )


@dataclass(frozen=True, slots=True)
class AcceptedUnit:
    unit_id: str
    attempt: int
    verdict_revision: int
    evidence_refs: tuple[EvidenceRef, ...] = ()
    checkpoint: CheckpointRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "attempt": self.attempt,
            "verdict_revision": self.verdict_revision,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint else None,
        }


@dataclass(frozen=True, slots=True)
class RejectedAttempt:
    unit_id: str
    attempt: int
    reason: str = ""
    verdict_revision: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "attempt": self.attempt,
            "reason": self.reason,
            "verdict_revision": self.verdict_revision,
        }


@dataclass(frozen=True, slots=True)
class PendingUnit:
    unit_id: str
    last_attempt: int = 0
    reason: str = "awaiting acceptance verdict"

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "last_attempt": self.last_attempt,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class BlockedUnit:
    unit_id: str
    reason: str = ""
    attempt: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"unit_id": self.unit_id, "reason": self.reason, "attempt": self.attempt}


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    accepted: tuple[AcceptedUnit, ...] = ()
    rejected_attempts: tuple[RejectedAttempt, ...] = ()
    pending: tuple[PendingUnit, ...] = ()
    blocked: tuple[BlockedUnit, ...] = ()
    verdicts: tuple[AcceptanceVerdict, ...] = ()
    event_ids: tuple[str, ...] = ()
    schema_version: int = 1

    @property
    def accepted_ids(self) -> tuple[str, ...]:
        return tuple(item.unit_id for item in self.accepted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": [item.to_dict() for item in self.accepted],
            "rejected_attempts": [item.to_dict() for item in self.rejected_attempts],
            "pending": [item.to_dict() for item in self.pending],
            "blocked": [item.to_dict() for item in self.blocked],
            "verdicts": [item.to_dict() for item in self.verdicts],
            "event_ids": list(self.event_ids),
            "schema_version": self.schema_version,
        }


@runtime_checkable
class ProgressStorePort(Protocol):
    def save(self, snapshot: ProgressSnapshot) -> None: ...

    def load(self) -> ProgressSnapshot | None: ...


class MemoryProgressStore:
    def __init__(self) -> None:
        self._snapshot: ProgressSnapshot | None = None

    def save(self, snapshot: ProgressSnapshot) -> None:
        self._snapshot = snapshot

    def load(self) -> ProgressSnapshot | None:
        return self._snapshot


class AcceptedProgressProjection:
    """Deterministic, acceptance-verdict-driven progress projection."""

    ELEMENT_CONTRACT = ElementContract(
        element_id="accepted_progress_projection",
        element_version=1,
        owner_repo="omodul",
        canonical_import="omodul.accepted_progress_projection",
        canonical_export="AcceptedProgressProjection",
        input_contract="GoalRunEvent-shaped facts, EvidenceRef, AcceptanceVerdict, CheckpointRef",
        output_contract="accepted/rejected/pending/blocked ProgressSnapshot",
        dependency_ports=("ProgressStorePort(optional)",),
        state_model="deterministic projection keyed by unit and verdict revision",
        persistence_model="optional snapshot materialization; event/verdict replay is canonical",
        authority_declaration=zero_authority(),
        async_contract="pure synchronous projection; caller owns event scheduling",
        failure_semantics="unverified and failed attempts remain non-accepted",
        recovery_semantics="rebuild from historical events and verdicts produces the same snapshot",
        observability_contract="event ids, verdict revisions and rejection reasons remain visible",
        compatibility_contract="does not import or depend on GoalRun implementation",
        conformance_suite=(
            "UNVERIFIED_RESULT_NOT_ACCEPTED",
            "FAILED_ATTEMPT_NOT_PROGRESS",
            "VERIFIED_RESULT_ACCEPTED",
            "VERDICT_REVISION_HANDLED",
            "REPLAY_DETERMINISTIC",
            "RESTART_REBUILD",
            "NO_ACCEPTANCE_AUTHORITY",
        ),
    )

    def __init__(self, *, store: ProgressStorePort | None = None) -> None:
        self.store = store
        self._events: dict[str, GoalRunEvent] = {}
        self._verdicts: dict[str, AcceptanceVerdict] = {}
        self._accepted: dict[str, AcceptedUnit] = {}
        self._rejected: dict[tuple[str, int], RejectedAttempt] = {}
        self._pending: dict[str, PendingUnit] = {}
        self._blocked: dict[str, BlockedUnit] = {}

    def apply_event(self, event: GoalRunEvent | FailureEvent | Mapping[str, Any]) -> None:
        if isinstance(event, FailureEvent):
            normalized = event.to_event()
        elif isinstance(event, GoalRunEvent):
            normalized = event
        else:
            normalized = GoalRunEvent.from_dict(_as_mapping(event))
        if normalized.event_id and normalized.event_id in self._events:
            return
        if normalized.event_id:
            self._events[normalized.event_id] = normalized
        unit_id = normalized.unit_id
        if not unit_id:
            return
        kind = normalized.kind.lower()
        payload = normalized.payload
        if "block" in kind:
            self._blocked[unit_id] = BlockedUnit(
                unit_id,
                str(payload.get("reason", kind)),
                normalized.attempt,
            )
            self._pending.pop(unit_id, None)
            self._persist()
            return
        if any(marker in kind for marker in ("fail", "error", "timeout", "cancel")):
            self._rejected[(unit_id, normalized.attempt)] = RejectedAttempt(
                unit_id,
                normalized.attempt,
                str(payload.get("reason", kind)),
            )
        if unit_id not in self._accepted:
            self._pending[unit_id] = PendingUnit(
                unit_id,
                max(
                    normalized.attempt,
                    self._pending.get(unit_id, PendingUnit(unit_id)).last_attempt,
                ),
            )
        self._persist()

    def apply_verdict(self, verdict: AcceptanceVerdict | Mapping[str, Any]) -> None:
        normalized = (
            verdict
            if isinstance(verdict, AcceptanceVerdict)
            else AcceptanceVerdict.from_dict(_as_mapping(verdict))
        )
        if not normalized.unit_id:
            raise ValueError("AcceptanceVerdict.unit_id must be non-empty")
        current = self._verdicts.get(normalized.unit_id)
        if current is not None and (normalized.revision, normalized.verdict_id) <= (
            current.revision,
            current.verdict_id,
        ):
            return
        self._verdicts[normalized.unit_id] = normalized
        if normalized.passed:
            self._accepted[normalized.unit_id] = AcceptedUnit(
                unit_id=normalized.unit_id,
                attempt=normalized.attempt,
                verdict_revision=normalized.revision,
                evidence_refs=normalized.evidence_refs,
            )
            self._pending.pop(normalized.unit_id, None)
            self._blocked.pop(normalized.unit_id, None)
        elif normalized.status.upper() in FAIL_STATUSES:
            self._accepted.pop(normalized.unit_id, None)
            self._rejected[(normalized.unit_id, normalized.attempt)] = RejectedAttempt(
                normalized.unit_id,
                normalized.attempt,
                normalized.reason or normalized.status,
                normalized.revision,
            )
            self._pending[normalized.unit_id] = PendingUnit(
                normalized.unit_id,
                normalized.attempt,
                normalized.reason or "awaiting a later verdict",
            )
        elif normalized.status.upper() == "BLOCKED":
            self._accepted.pop(normalized.unit_id, None)
            self._pending.pop(normalized.unit_id, None)
            self._blocked[normalized.unit_id] = BlockedUnit(
                normalized.unit_id, normalized.reason, normalized.attempt
            )
        else:
            if normalized.unit_id not in self._accepted:
                self._pending[normalized.unit_id] = PendingUnit(
                    normalized.unit_id, normalized.attempt, normalized.reason or "pending verdict"
                )
        self._persist()

    def snapshot(self) -> ProgressSnapshot:
        return ProgressSnapshot(
            accepted=tuple(self._accepted[key] for key in sorted(self._accepted)),
            rejected_attempts=tuple(self._rejected[key] for key in sorted(self._rejected)),
            pending=tuple(self._pending[key] for key in sorted(self._pending)),
            blocked=tuple(self._blocked[key] for key in sorted(self._blocked)),
            verdicts=tuple(self._verdicts[key] for key in sorted(self._verdicts)),
            event_ids=tuple(sorted(self._events)),
        )

    def accepted(self) -> list[AcceptedUnit]:
        return list(self.snapshot().accepted)

    def rejected(self) -> list[RejectedAttempt]:
        return list(self.snapshot().rejected_attempts)

    def pending(self) -> list[PendingUnit]:
        return list(self.snapshot().pending)

    def blocked(self) -> list[BlockedUnit]:
        return list(self.snapshot().blocked)

    def rebuild(
        self,
        events: Iterable[GoalRunEvent | FailureEvent | Mapping[str, Any]],
        verdicts: Iterable[AcceptanceVerdict | Mapping[str, Any]] = (),
        checkpoints: Iterable[CheckpointRef | Mapping[str, Any]] = (),
    ) -> ProgressSnapshot:
        """Reset and replay facts in canonical order; safe after process restart."""

        self._events.clear()
        self._verdicts.clear()
        self._accepted.clear()
        self._rejected.clear()
        self._pending.clear()
        self._blocked.clear()
        event_list = list(events)
        event_list.sort(key=_replay_key)
        for event in event_list:
            self.apply_event(event)
        checkpoint_map: dict[str, CheckpointRef] = {}
        for checkpoint_item in checkpoints:
            normalized = (
                checkpoint_item
                if isinstance(checkpoint_item, CheckpointRef)
                else CheckpointRef(
                    checkpoint_id=str(
                        checkpoint_item.get("checkpoint_id", checkpoint_item.get("id", ""))
                    ),
                    unit_id=str(checkpoint_item.get("unit_id", "")),
                    revision=str(checkpoint_item.get("revision", "")),
                    reference=str(checkpoint_item.get("reference", checkpoint_item.get("uri", ""))),
                )
            )
            checkpoint_map[normalized.unit_id] = normalized
        verdict_list = list(verdicts)
        verdict_list.sort(key=_verdict_key)
        for verdict_item in verdict_list:
            self.apply_verdict(verdict_item)
        if checkpoint_map:
            self._accepted = {
                key: AcceptedUnit(
                    key,
                    value.attempt,
                    value.verdict_revision,
                    value.evidence_refs,
                    checkpoint_map[key],
                )
                if key in checkpoint_map
                else value
                for key, value in self._accepted.items()
            }
        self._persist()
        return self.snapshot()

    def _persist(self) -> None:
        if self.store is not None:
            self.store.save(self.snapshot())


def _replay_key(item: GoalRunEvent | FailureEvent | Mapping[str, Any]) -> tuple[int, float, str]:
    if isinstance(item, FailureEvent):
        return (0, item.timestamp, item.event_id)
    event = item if isinstance(item, GoalRunEvent) else GoalRunEvent.from_dict(_as_mapping(item))
    return (
        event.sequence if event.sequence is not None else 2**63,
        event.timestamp,
        event.event_id,
    )


def _verdict_key(item: AcceptanceVerdict | Mapping[str, Any]) -> tuple[str, int, str]:
    verdict = (
        item
        if isinstance(item, AcceptanceVerdict)
        else AcceptanceVerdict.from_dict(_as_mapping(item))
    )
    return verdict.unit_id, verdict.revision, verdict.verdict_id


__all__ = [
    "AcceptanceVerdict",
    "AcceptedProgressProjection",
    "AcceptedUnit",
    "BlockedUnit",
    "CheckpointRef",
    "EvidenceRef",
    "FailureEvent",
    "GoalRunEvent",
    "MemoryProgressStore",
    "PendingUnit",
    "ProgressSnapshot",
    "ProgressStorePort",
    "RejectedAttempt",
]
