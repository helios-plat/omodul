"""provenance_w3c — W3C PROV-O 溯源 (3O operator)。

给实体/决策/知识记录生成 W3C PROV-O 溯源图 (Entity/Activity/Agent +
used/wasGeneratedBy/wasDerivedFrom/wasAttributedTo), 可导出 JSON 或 RDF Turtle。
与 decision_ledger 的 prov_o 导出互补: 本 operator 是通用溯源 (任意实体),
支持从 decision_ledger / 概念图 / 操作日志生成。

纯确定性, 零 LLM。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

_PROV_NS = "http://www.w3.org/ns/prov#"
_VEYA_NS = "https://veya.ai/ns#"


class ProvenanceW3cConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "provenance_w3c"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class ProvenanceW3cInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    entities: list[dict] = []      # [{id, type, value, attrs}]
    activities: list[dict] = []    # [{id, type, started, ended, attrs}]
    relations: list[dict] = []     # [{kind: used|wasGeneratedBy|wasDerivedFrom|wasAttributedTo, src, dst, attrs}]
    format: str = "json"           # json | turtle
    backend: Any | None = None


# ── RDF Turtle 导出 ───────────────────────────────────────────────────

def _turtle_escape(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def _to_turtle(inp: ProvenanceW3cInput) -> str:
    lines = [
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "@prefix veya: <https://veya.ai/ns#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
    ]
    for e in inp.entities:
        eid = e.get("id", "")
        lines.append(f"<{eid}> a prov:Entity, veya:{e.get('type', 'Entity')} ;")
        if e.get("value") is not None:
            lines.append(f"    prov:value {_turtle_escape(str(e['value']))} ;")
        for k, v in (e.get("attrs") or {}).items():
            lines.append(f"    veya:{k} {_turtle_escape(str(v))} ;")
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.append("")
    for a in inp.activities:
        aid = a.get("id", "")
        lines.append(f"<{aid}> a prov:Activity ;")
        if a.get("started"):
            lines.append(f"    prov:startedAtTime {_turtle_escape(str(a['started']))} ;")
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.append("")
    for r in inp.relations:
        kind, src, dst = r.get("kind", ""), r.get("src", ""), r.get("dst", "")
        if kind == "used":
            lines.append(f"<{src}> prov:used <{dst}> .")
        elif kind == "wasGeneratedBy":
            lines.append(f"<{src}> prov:wasGeneratedBy <{dst}> .")
        elif kind == "wasDerivedFrom":
            lines.append(f"<{src}> prov:wasDerivedFrom <{dst}> .")
        elif kind == "wasAttributedTo":
            lines.append(f"<{src}> prov:wasAttributedTo <{dst}> .")
        lines.append("")
    return "\n".join(lines)


# ── operator 入口 ─────────────────────────────────────────────────────

def provenance_w3c(
    config: ProvenanceW3cConfig,
    input_data: ProvenanceW3cInput,
    output_dir: Path | None = None,
    *,
    on_step: Any = None,
) -> dict:
    """生成 W3C PROV-O 溯源图并导出 (json 或 turtle)。

    backend 注入 (stratum DAO) 时, 可从现有记录自动聚合溯源
    (list_provenance() → [{kind, src, dst, attrs}] 追加)。
    """
    config = ProvenanceW3cConfig.model_validate(config)
    input_data = ProvenanceW3cInput.model_validate(input_data)
    out_dir = Path(output_dir) if output_dir else Path.cwd()
    trail = Trail()
    if on_step:
        try:
            on_step("provenance", "start")
        except TypeError:
            on_step({"step": "provenance", "state": "start"})

    relations = list(input_data.relations)
    if input_data.backend is not None:
        try:
            extra = input_data.backend.list_provenance() or []
            relations.extend(extra)
        except Exception:  # noqa: BLE001
            pass

    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = input_data.format.lower()
    if fmt == "turtle":
        text = _to_turtle(input_data)
        path = out_dir / "provenance.ttl"
        path.write_text(text, encoding="utf-8")
        payload = {"format": "turtle", "path": str(path), "turtle": text[:2000]}
    else:
        prov_entities = [
            {
                "@id": f"veya:{e['id']}" if not str(e.get('id', '')).startswith(('veya:', 'prov:')) else e['id'],
                "@type": ["prov:Entity", f"veya:{e.get('type', 'Entity')}"],
                "prov:value": e.get("value"),
                **{f"veya:{k}": v for k, v in (e.get("attrs") or {}).items()},
            }
            for e in input_data.entities
        ]
        prov_activities = [
            {
                "@id": f"veya:{a['id']}" if not str(a.get('id', '')).startswith(('veya:', 'prov:')) else a['id'],
                "@type": ["prov:Activity"],
                **{f"veya:{k}": v for k, v in (a.get("attrs") or {}).items()},
            }
            for a in input_data.activities
        ]
        payload = {
            "format": "json",
            "prefix": {"prov": _PROV_NS, "veya": _VEYA_NS},
            "entities": prov_entities,
            "activities": prov_activities,
            "relations": relations,
        }
        path = out_dir / "provenance.json"
        path.write_text(
            __import__("json").dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    trail.record(event="provenance_export", format=fmt,
                 entities=len(input_data.entities), relations=len(relations))
    trail_path = trail.write(out_dir)
    findings = {
        "path": str(path),
        "entities": len(input_data.entities),
        "activities": len(input_data.activities),
        "relations": len(relations),
    }
    return build_result(
        status="completed", error=None,
        fingerprint=compute_fingerprint(findings),
        trail=trail, trail_path=trail_path,
        cost_usd=0.0, findings=findings,
    )
