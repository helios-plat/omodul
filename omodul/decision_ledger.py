"""decision_ledger — 决策智能账本 (3O operator)。

移植 semantica decision intelligence 核心 (Record → Link → Query → Govern → Audit),
精简为 3O operator 范式: Config → Input → output_dir/backend → dict(findings/decision_trail)。

- 决策是一等对象: record_decision → add_causal_relationship(CAUSED/INFLUENCED/
  PRECEDENT_FOR) → find_similar_decisions(先例检索) → trace_decision_chain /
  analyze_decision_impact → check_decision_rules(策略门) → export(json/csv/prov_o)
- 纯确定性 (零 LLM): 先例检索用 TF 加权关键词重叠; embedding 可选注入。
- 存储: backend 注入 (stratum DAO: put/get/list) 或 output_dir JSON 落盘 (无 DB)。
"""

from __future__ import annotations

import csv
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

_RELATION_TYPES = {"CAUSED", "INFLUENCED", "PRECEDENT_FOR"}


# ── 数据模型 ──────────────────────────────────────────────────────────

@dataclass
class Decision:
    decision_id: str
    category: str
    scenario: str
    reasoning: str
    outcome: str
    confidence: float
    decision_maker: str
    timestamp: str
    source_refs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "category": self.category,
            "scenario": self.scenario,
            "reasoning": self.reasoning,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "decision_maker": self.decision_maker,
            "timestamp": self.timestamp,
            "source_refs": self.source_refs,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Decision":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class DecisionRelation:
    src_id: str
    dst_id: str
    relationship_type: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "src_id": self.src_id,
            "dst_id": self.dst_id,
            "relationship_type": self.relationship_type,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DecisionRelation":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Config / Input ────────────────────────────────────────────────────

class DecisionLedgerConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "decision_ledger"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class DecisionLedgerInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    action: str  # record | link | query_similar | trace | impact | rules | export | list
    # record
    category: str = ""
    scenario: str = ""
    reasoning: str = ""
    outcome: str = ""
    confidence: float = 0.5
    decision_maker: str = ""
    source_refs: list[str] = field(default_factory=list)
    # link
    src_id: str = ""
    dst_id: str = ""
    relationship_type: str = ""
    # query / trace / impact
    query_text: str = ""
    decision_id: str = ""
    max_results: int = 5
    max_depth: int = 3
    direction: str = "both"  # upstream | downstream | both
    # rules
    rules: list[dict] = field(default_factory=list)  # [{rule, params...}]
    # export
    format: str = "json"  # json | csv | prov_o
    backend: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ── 先例相似度 (TF 加权关键词重叠, 零 LLM) ───────────────────────────

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tf_tokens(text: str) -> dict[str, float]:
    toks = _TOKEN_RE.findall((text or "").lower())
    if not toks:
        return {}
    counts: dict[str, int] = {}
    for t in toks:
        counts[t] = counts.get(t, 0) + 1
    n = max(len(toks), 1)
    return {t: c / n for t, c in counts.items()}


def _similarity(a: str, b: str) -> float:
    """TF 加权 Jaccard (确定性, 0..1)。"""
    ta, tb = _tf_tokens(a), _tf_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = sum(min(ta.get(t, 0), tb.get(t, 0)) for t in set(ta) | set(tb))
    union = sum(max(ta.get(t, 0), tb.get(t, 0)) for t in set(ta) | set(tb))
    return inter / union if union else 0.0


# ── 存储协议 (backend 注入 或 output_dir JSON) ───────────────────────

class FileLedgerBackend:
    """无 DB 落盘: output_dir/ledger.json (单测/本地可用)。"""

    def __init__(self, output_dir: Path) -> None:
        self._path = output_dir / "ledger.json"
        output_dir.mkdir(parents=True, exist_ok=True)
        self._data: dict = {"decisions": {}, "relations": []}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

    def put_decision(self, d: Decision) -> None:
        self._data["decisions"][d.decision_id] = d.to_dict()
        self._flush()

    def get_decision(self, decision_id: str) -> Decision | None:
        raw = self._data["decisions"].get(decision_id)
        return Decision.from_dict(raw) if raw else None

    def list_decisions(self) -> list[Decision]:
        return [Decision.from_dict(v) for v in self._data["decisions"].values()]

    def put_relation(self, r: DecisionRelation) -> None:
        self._data["relations"].append(r.to_dict())
        self._flush()

    def list_relations(self) -> list[DecisionRelation]:
        return [DecisionRelation.from_dict(v) for v in self._data["relations"]]

    def _flush(self) -> None:
        self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_backend(backend: Any, output_dir: Path) -> Any:
    """backend 注入 (stratum DAO) 或文件后端。"""
    if backend is not None:
        return backend
    return FileLedgerBackend(output_dir)


# ── 核心操作 ─────────────────────────────────────────────────────────

def _record(ledger: Any, inp: DecisionLedgerInput, trail: Trail) -> dict:
    if not inp.scenario.strip() or not inp.outcome.strip():
        raise ValueError("record 需要 scenario 与 outcome (可验收的决策描述)")
    if not 0 <= inp.confidence <= 1:
        raise ValueError("confidence 必须在 0..1")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    d = Decision(
        decision_id=uuid.uuid4().hex[:12],
        category=inp.category.strip() or "general",
        scenario=inp.scenario.strip(),
        reasoning=inp.reasoning.strip(),
        outcome=inp.outcome.strip(),
        confidence=inp.confidence,
        decision_maker=inp.decision_maker.strip() or "master",
        timestamp=now,
        source_refs=inp.source_refs,
        metadata=inp.metadata,
    )
    ledger.put_decision(d)
    trail.record(event="record_decision", decision_id=d.decision_id, category=d.category)
    return {"decision_id": d.decision_id, "decision": d.to_dict()}


def _link(ledger: Any, inp: DecisionLedgerInput, trail: Trail) -> dict:
    rtype = inp.relationship_type.upper()
    if rtype not in _RELATION_TYPES:
        raise ValueError(f"relationship_type 必须是 {'/'.join(sorted(_RELATION_TYPES))}")
    if ledger.get_decision(inp.src_id) is None:
        raise ValueError(f"src 决策不存在: {inp.src_id}")
    if ledger.get_decision(inp.dst_id) is None:
        raise ValueError(f"dst 决策不存在: {inp.dst_id}")
    r = DecisionRelation(src_id=inp.src_id, dst_id=inp.dst_id, relationship_type=rtype, metadata=inp.metadata)
    ledger.put_relation(r)
    trail.record(event="link_causal", src=inp.src_id, dst=inp.dst_id, type=rtype)
    return {"relation": r.to_dict()}


def _query_similar(ledger: Any, inp: DecisionLedgerInput, trail: Trail) -> dict:
    decisions = ledger.list_decisions()
    scored = [
        ((_similarity(inp.query_text, d.scenario) + _similarity(inp.query_text, d.reasoning)) / 2, d)
        for d in decisions
    ]
    scored.sort(key=lambda x: x[0], reverse=True)  # 只按分数排序 (Decision 不可比)
    top = [d.to_dict() for s, d in scored[: inp.max_results]]
    trail.record(event="query_precedents", query=inp.query_text[:80], hits=len(top))
    return {"precedents": top, "hits": len(top)}


def _build_graph(ledger: Any) -> dict[str, tuple[list[str], list[str]]]:
    """决策图: id → (upstream_srcs, downstream_dsts)。"""
    g: dict[str, tuple[list[str], list[str]]] = {}
    for d in ledger.list_decisions():
        g.setdefault(d.decision_id, ([], []))
    for r in ledger.list_relations():
        g.setdefault(r.src_id, ([], []))
        g.setdefault(r.dst_id, ([], []))
        g[r.src_id][1].append(r.dst_id)
        g[r.dst_id][0].append(r.src_id)
    return g


def _trace(ledger: Any, inp: DecisionLedgerInput, trail: Trail) -> dict:
    g = _build_graph(ledger)
    if inp.decision_id not in g:
        raise ValueError(f"决策不存在: {inp.decision_id}")
    seen: dict[str, int] = {}
    queue = [(inp.decision_id, 0)]
    while queue:
        cur, depth = queue.pop(0)
        if cur in seen:
            continue
        seen[cur] = depth
        if depth >= inp.max_depth:
            continue
        up, down = g[cur]
        if inp.direction in ("upstream", "both"):
            for n in up:
                if n not in seen:
                    queue.append((n, depth + 1))
        if inp.direction in ("downstream", "both"):
            for n in down:
                if n not in seen:
                    queue.append((n, depth + 1))
    chain = [{"decision_id": cid, "depth": dep} for cid, dep in sorted(seen.items(), key=lambda x: x[1])]
    trail.record(event="trace_chain", decision_id=inp.decision_id, nodes=len(chain))
    return {"decision_id": inp.decision_id, "chain": chain}


def _impact(ledger: Any, inp: DecisionLedgerInput, trail: Trail) -> dict:
    """下游影响图 (从该决策出发的因果后代, 带路径)。"""
    g = _build_graph(ledger)
    if inp.decision_id not in g:
        raise ValueError(f"决策不存在: {inp.decision_id}")
    downstream: list[dict] = []
    queue = [(inp.decision_id, [])]
    seen: set[str] = set()
    while queue:
        cur, path = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        for n in g[cur][1]:
            new_path = [*path, n]
            downstream.append({"from": cur, "to": n, "path": new_path})
            queue.append((n, new_path))
    trail.record(event="analyze_impact", decision_id=inp.decision_id, edges=len(downstream))
    return {"decision_id": inp.decision_id, "impact_edges": downstream}


def _check_rules(ledger: Any, inp: DecisionLedgerInput, trail: Trail) -> dict:
    """策略门: 对指定决策 (或全部) 执行规则, 返回 pass/fail。"""
    if inp.decision_id:
        targets = [ledger.get_decision(inp.decision_id)]
    else:
        targets = ledger.list_decisions()
    results = []
    for d in targets:
        if d is None:
            continue
        violations = []
        for rule in inp.rules or []:
            rtype = str(rule.get("rule", ""))
            if rtype == "confidence_min":
                if d.confidence < float(rule.get("min", 0.0)):
                    violations.append(f"confidence {d.confidence:.2f} < {rule.get('min')}")
            elif rtype == "category_allow":
                allow = set(rule.get("allow", []) or [])
                if allow and d.category not in allow:
                    violations.append(f"category '{d.category}' 不在允许集")
            elif rtype == "outcome_block":
                block = set(rule.get("block", []) or [])
                if d.outcome in block:
                    violations.append(f"outcome '{d.outcome}' 被禁止")
            elif rtype == "maker_allow":
                allow = set(rule.get("allow", []) or [])
                if allow and d.decision_maker not in allow:
                    violations.append(f"decision_maker '{d.decision_maker}' 不在允许集")
        results.append({
            "decision_id": d.decision_id,
            "category": d.category,
            "outcome": d.outcome,
            "pass": len(violations) == 0,
            "violations": violations,
        })
    passed = sum(1 for r in results if r["pass"])
    trail.record(event="check_rules", checked=len(results), passed=passed)
    return {"checked": len(results), "passed": passed, "results": results}


def _export(ledger: Any, inp: DecisionLedgerInput, output_dir: Path, trail: Trail) -> dict:
    decisions = [d.to_dict() for d in ledger.list_decisions()]
    relations = [r.to_dict() for r in ledger.list_relations()]
    output_dir.mkdir(parents=True, exist_ok=True)
    fmt = inp.format.lower()
    if fmt == "csv":
        path = output_dir / "decisions.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(decisions[0].keys()) if decisions else ["decision_id"])
            writer.writeheader()
            writer.writerows(decisions)
        rel_path = output_dir / "relations.csv"
        with rel_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["src_id", "dst_id", "relationship_type"])
            writer.writeheader()
            writer.writerows(relations)
        out = {"path": str(path), "relations_path": str(rel_path), "decisions": len(decisions), "relations": len(relations)}
    elif fmt == "prov_o":
        out = _export_prov_o(decisions, relations, output_dir)
    else:
        path = output_dir / "ledger_export.json"
        path.write_text(json.dumps({"decisions": decisions, "relations": relations}, ensure_ascii=False, indent=2), encoding="utf-8")
        out = {"path": str(path), "decisions": len(decisions), "relations": len(relations)}
    trail.record(event="export", format=fmt, decisions=len(decisions))
    return out


def _export_prov_o(decisions: list[dict], relations: list[dict], output_dir: Path) -> dict:
    """W3C PROV-O 风格导出 (JSON 形态; RDF Turtle 由 provenance_w3c operator 提供)。"""
    entities = [
        {
            "@id": f"decision:{d['decision_id']}",
            "@type": ["prov:Entity", "veya:Decision"],
            "veya:category": d["category"],
            "veya:outcome": d["outcome"],
            "prov:value": d["reasoning"],
            "prov:wasAttributedTo": f"agent:{d['decision_maker']}",
            "prov:generatedAtTime": d["timestamp"],
            "prov:confidence": d["confidence"],
            "veya:sourceRef": d["source_refs"],
        }
        for d in decisions
    ]
    activities = []
    for r in relations:
        activities.append({
            "@id": f"relation:{r['src_id']}->{r['dst_id']}",
            "@type": "prov:Activity",
            "prov:used": f"decision:{r['src_id']}",
            "prov:wasInformedBy": f"decision:{r['dst_id']}",
            "veya:relationshipType": r["relationship_type"],
        })
    path = output_dir / "provenance_prov-o.json"
    payload = {"prefix": {"prov": "http://www.w3.org/ns/prov#", "veya": "https://veya.ai/ns#"}, "entities": entities, "activities": activities}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(path), "entities": len(entities), "activities": len(activities)}


# ── operator 入口 ─────────────────────────────────────────────────────

def decision_ledger(
    config: DecisionLedgerConfig,
    input_data: DecisionLedgerInput,
    output_dir: Path | None = None,
    *,
    on_step: Any = None,
) -> dict:
    """决策智能账本入口 (3O operator)。

    action: record / link / query_similar / trace / impact / rules / export / list
    backend: 注入 stratum DAO (put_decision/get_decision/list_decisions/
    put_relation/list_relations); None → output_dir JSON 落盘。
    """
    config = DecisionLedgerConfig.model_validate(config)
    input_data = DecisionLedgerInput.model_validate(input_data)
    out_dir = Path(output_dir) if output_dir else Path.cwd()
    trail = Trail()
    ledger = _resolve_backend(input_data.backend, out_dir)
    action = input_data.action
    if on_step:
        try:
            on_step(action, "start")
        except TypeError:
            on_step({"step": action, "state": "start"})

    if action == "list":
        decisions = [d.to_dict() for d in ledger.list_decisions()]
        return build_result(status="completed", error=None, trail=trail, findings={"decisions": decisions, "count": len(decisions)})
    handlers = {
        "record": lambda: _record(ledger, input_data, trail),
        "link": lambda: _link(ledger, input_data, trail),
        "query_similar": lambda: _query_similar(ledger, input_data, trail),
        "trace": lambda: _trace(ledger, input_data, trail),
        "impact": lambda: _impact(ledger, input_data, trail),
        "rules": lambda: _check_rules(ledger, input_data, trail),
        "export": lambda: _export(ledger, input_data, out_dir, trail),
    }
    if action not in handlers:
        raise ValueError(f"action 非法: {action} (record/link/query_similar/trace/impact/rules/export/list)")
    findings = handlers[action]()
    trail_path = trail.write(out_dir)
    return build_result(
        status="completed", error=None,
        fingerprint=compute_fingerprint({"action": action, **findings}),
        trail=trail, trail_path=trail_path,
        cost_usd=0.0, findings=findings,
    )
