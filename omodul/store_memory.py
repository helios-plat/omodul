"""omodul.store_memory — Store query/case/solution_strategy memory per HOS.

3O layer: omodul (≥2 operations: validation + storage, business transaction semantics).
Three memory types from 13-Learning-SPEC: query, case, solution_strategy.
Pillars: {fingerprint, decision_trail}
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import ConfigDict

from omodul._base_config import BaseConfig

_enabled_pillars: set[str] = {"fingerprint", "decision_trail"}

VALID_MEMORY_TYPES = {"query", "case", "solution_strategy"}


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _sha256_hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class StoreMemoryConfig(BaseConfig):
    """store_memory configuration."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    _omodul_name: ClassVar[str] = "store_memory"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"memory_type", "content"}
    backend: Any = None  # StorageBackend


def _build_ku(memory_type: str, content: dict, project_id: str) -> dict:
    """Construct a KU dict for the given memory type."""
    memory_id = f"MEM-{uuid.uuid4()}"

    if memory_type == "query":
        natural_text = content.get("text", content.get("question", str(content)))
        symbolic_form = None
    elif memory_type == "case":
        natural_text = content.get(
            "description",
            f"Case: {content.get('problem', str(content)[:200])}",
        )
        symbolic_form = {
            "problem": content.get("problem"),
            "solution": content.get("solution"),
            "context": content.get("context"),
        }
    else:  # solution_strategy
        natural_text = content.get(
            "description",
            content.get("title", str(content)[:200]),
        )
        symbolic_form = {
            "title": content.get("title"),
            "description": content.get("description"),
            "content": content.get("content"),
        }

    return {
        "ku_id": memory_id,
        "knowledge_type": memory_type,
        "natural_text": natural_text,
        "symbolic_form": symbolic_form,
        "vector": None,
        "vector_frozen": False,
        "epistemic_status": {
            "grade": "unverified",
            "source": "store_memory",
            "defeaters": [],
            "verified": False,
        },
        "provenance": {"source": "store_memory", "chunk_id": None},
        "project_id": project_id,
    }


def store_memory(
    config: StoreMemoryConfig | dict,
    input_data: dict,
    output_dir: str | None = None,
    *,
    on_step: Callable | None = None,
) -> dict:
    """Store a memory unit (query/case/solution_strategy) per HOS-001.

    config:     StoreMemoryConfig (with backend)
    input_data: {"memory_type": str, "content": dict, "project_id": str}
    output_dir: decision_trail.json write directory (None = no file)
    on_step:    per-step callback (optional)

    Returns standard omodul dict:
        findings:       memory_id (None on failure)
        status:         "completed" | "failed"
        error:          failure reason (None on success)
        fingerprint:    content fingerprint
        decision_trail: execution trail
        report_path:    None
        cost_usd:       0.0
    """
    if isinstance(config, dict):
        config = StoreMemoryConfig(**config) if config else StoreMemoryConfig()
    trail: list[dict] = []
    fingerprint: str | None = None
    memory_id = None
    status = "failed"
    error = None

    def _emit(step_dict: dict) -> None:
        trail.append(step_dict)
        if on_step is not None:
            on_step(step_dict)

    try:
        backend = config.backend
        if backend is None:
            raise ValueError("backend is required but not provided")

        memory_type = input_data.get("memory_type")
        content = input_data.get("content", {})
        project_id = input_data.get("project_id", "default")

        # step 1: validate memory_type
        _emit({"step": "validate_memory_type", "memory_type": memory_type})
        if memory_type not in VALID_MEMORY_TYPES:
            raise ValueError(
                f"Invalid memory_type '{memory_type}'; must be one of {sorted(VALID_MEMORY_TYPES)}"
            )
        trail[-1]["result"] = "pass"

        # step 2: compute content fingerprint (fingerprint pillar)
        basis = {"memory_type": memory_type, "content": content}
        fingerprint = _sha256_hash(_canonical_json(basis))
        _emit({"step": "compute_fingerprint", "fingerprint": fingerprint})

        # step 3: construct KU for memory type
        ku = _build_ku(memory_type, content, project_id)
        memory_id = ku["ku_id"]
        _emit({"step": "build_ku", "memory_id": memory_id, "memory_type": memory_type})

        # step 4: store via backend
        backend.put_node(memory_id, ku)
        _emit({"step": "put_node", "memory_id": memory_id, "result": "ok"})

        status = "completed"
    except Exception as e:
        error = {"code": "ERR_STORE_MEMORY", "message": str(e)}
        _emit({"step": "abort", "error": error})
    finally:
        decision_trail = {
            "omodul": "store_memory",
            "enabled_pillars": sorted(_enabled_pillars),
            "memory_id": memory_id,
            "status": status,
            "steps": trail,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            with open(os.path.join(output_dir, "decision_trail.json"), "w", encoding="utf-8") as f:
                json.dump(decision_trail, f, ensure_ascii=False, indent=2)

    return {
        "findings": memory_id if status == "completed" else None,
        "status": status,
        "error": error,
        "fingerprint": fingerprint,
        "decision_trail": decision_trail,
        "report_path": None,
        "cost_usd": 0.0,
    }
