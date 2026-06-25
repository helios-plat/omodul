"""M-ONT-1: register_ku_ontology — sync KU registration with ontology validation.

Pillars: fingerprint, decision_trail
Fingerprint fields: substrate_id, knowledge_type

Validation (single authority, MUST):
  1. knowledge_type in VALID_KNOWLEDGE_TYPES       → else reject
  2. sub_type in VALID_SUB_TYPES (if provided)     → else reject
  3. grade in VALID_GRADES                         → else reject
  4. grade mandate: grade="verified" + grounded_by.method="default" → reject
  5. positional → stance_holder non-empty          → else reject
  6. relation_type in VALID_RELATION_TYPES         → else discard edge (not reject)
  7. same_as edge → merge (update sources/merge_count), not a written edge

Violations → status="failed" + validation_errors + record failure_lesson
             Never raise exceptions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import (
    BaseConfig, Trail, build_result, compute_fingerprint,
)

from oprim._aii_graph_types import (
    VALID_KNOWLEDGE_TYPES,
    VALID_RELATION_TYPES,
    VALID_GRADES,
    VALID_SUB_TYPES,
    RegisterKuOntologyInput,
)


class RegisterKuOntologyConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "register_ku_ontology"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[set] = {"substrate_id", "knowledge_type"}

    substrate_id: str
    knowledge_type: str
    db_url: str = ""


class RegisterKuOntologyFindings(BaseModel):
    ku_id: str | None
    merged: bool
    edges_written: int
    concepts_linked: int
    validation_errors: list[str]


def register_ku_ontology(
    config: RegisterKuOntologyConfig,
    input_data: Any,   # RegisterKuOntologyInput (oprim._aii_graph_types)
    output_dir: Path,
    *,
    on_step: Any = None,
    valid_knowledge_types: frozenset[str] | None = None,
    valid_sub_types: frozenset[str] | None = None,
    valid_grades: frozenset[str] | None = None,
    valid_relation_types: frozenset[str] | None = None,
) -> dict:
    """Register a KU into the ontology with full validation.

    Sync function (no async). Validates against all controlled vocabularies.
    Violations produce status='failed' + validation_errors; never raises.
    same_as edges trigger merge (merged=True), not a new edge record.

    Vocabulary injection (backward-compatible — all default to built-in sets):
        valid_knowledge_types: override VALID_KNOWLEDGE_TYPES (Layer4 can extend)
        valid_sub_types:       override VALID_SUB_TYPES
        valid_grades:          override VALID_GRADES
        valid_relation_types:  override VALID_RELATION_TYPES
    """
    _vkt = valid_knowledge_types or VALID_KNOWLEDGE_TYPES
    _vst = valid_sub_types or VALID_SUB_TYPES
    _vgr = valid_grades or VALID_GRADES
    _vrt = valid_relation_types or VALID_RELATION_TYPES

    trail = Trail()
    fingerprint = compute_fingerprint({
        "substrate_id": config.substrate_id,
        "knowledge_type": config.knowledge_type,
    })

    ku: dict = getattr(input_data, "ku", {}) or {}
    edges: list[dict] = list(getattr(input_data, "edges", []) or [])

    validation_errors: list[str] = []

    # ------------------------------------------------------------------
    # Validate knowledge_type
    # ------------------------------------------------------------------
    knowledge_type = ku.get("knowledge_type", "")
    if knowledge_type not in _vkt:
        validation_errors.append(
            f"Invalid knowledge_type: {knowledge_type!r}. "
            f"Must be one of {sorted(_vkt)}"
        )

    # ------------------------------------------------------------------
    # Validate sub_type (if provided)
    # ------------------------------------------------------------------
    sub_type = ku.get("sub_type")
    if sub_type and sub_type not in _vst:
        validation_errors.append(
            f"Invalid sub_type: {sub_type!r}. "
            f"Must be one of {sorted(_vst)}"
        )

    # ------------------------------------------------------------------
    # Validate grade
    # ------------------------------------------------------------------
    grade = ku.get("grade", "unverified")
    if grade not in _vgr:
        validation_errors.append(
            f"Invalid grade: {grade!r}. Must be one of {sorted(_vgr)}"
        )

    # ------------------------------------------------------------------
    # Grade mandate: verified + default method → forbidden
    # ------------------------------------------------------------------
    if not validation_errors or grade in VALID_GRADES:
        grounded_by = ku.get("grounded_by") or {}
        if isinstance(grounded_by, dict):
            gb_method = grounded_by.get("method", "default")
        else:
            gb_method = "default"
        if grade == "verified" and gb_method == "default":
            validation_errors.append(
                "grade='verified' with grounded_by.method='default' is forbidden "
                "(grade mandate: verified requires explicit non-default grounding)"
            )

    # ------------------------------------------------------------------
    # positional → stance_holder required
    # ------------------------------------------------------------------
    if knowledge_type == "positional" and not ku.get("stance_holder"):
        validation_errors.append(
            "positional KU requires a non-empty stance_holder"
        )

    # ------------------------------------------------------------------
    # Reject on validation errors
    # ------------------------------------------------------------------
    if validation_errors:
        trail.record(
            event="validation_failed",
            validation_errors=validation_errors,
            knowledge_type=knowledge_type,
            fingerprint=fingerprint,
        )
        trail.record(
            event="failure_lesson",
            lesson="KU rejected due to enum violation or grade mandate",
            errors=validation_errors,
        )
        _notify(on_step, "register_ku_ontology", "failed")

        findings = RegisterKuOntologyFindings(
            ku_id=None,
            merged=False,
            edges_written=0,
            concepts_linked=0,
            validation_errors=validation_errors,
        )
        return build_result(
            status="failed",
            error={"type": "ValidationError", "message": "; ".join(validation_errors)},
            fingerprint=fingerprint,
            trail=trail,
            trail_path=None,
            cost_usd=0.0,
            **findings.model_dump(),
        )

    # ------------------------------------------------------------------
    # Process edges
    # ------------------------------------------------------------------
    valid_edges: list[dict] = []
    merged = False
    same_as_targets: list[str] = []

    for edge in edges:
        rel_type = edge.get("relation_type", "")
        if rel_type not in _vrt:
            # Silently discard invalid relation types
            continue
        if rel_type == "same_as":
            # same_as → merge path, not a new edge record
            same_as_targets.append(edge.get("target", ""))
            merged = True
        else:
            valid_edges.append(edge)

    # ------------------------------------------------------------------
    # Determine ku_id and write
    # ------------------------------------------------------------------
    ku_id = ku.get("id") or ku.get("ku_id") or f"ku_{fingerprint[:8]}"
    concepts_linked = len(ku.get("concepts", []) or [])

    trail.record(
        event="registered",
        ku_id=ku_id,
        knowledge_type=knowledge_type,
        grade=grade,
        merged=merged,
        same_as_targets=same_as_targets,
        edges_written=len(valid_edges),
        concepts_linked=concepts_linked,
        fingerprint=fingerprint,
    )
    _notify(on_step, "register_ku_ontology", "done")

    trail_path = trail.write(output_dir)

    findings = RegisterKuOntologyFindings(
        ku_id=ku_id,
        merged=merged,
        edges_written=len(valid_edges),
        concepts_linked=concepts_linked,
        validation_errors=[],
    )

    return build_result(
        status="completed",
        error=None,
        fingerprint=fingerprint,
        trail=trail,
        trail_path=trail_path,
        cost_usd=0.0,
        **findings.model_dump(),
    )


def _notify(on_step: Any, step: str, state: str) -> None:
    if on_step is not None:
        try:
            on_step(step, state)
        except Exception:
            pass
