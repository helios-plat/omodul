"""omodul.graphrag_query — GraphRAG-style knowledge retrieval with epistemic status.

3O layer: omodul (≥2 oprim composition: vector_encode + entity_graph_search, business semantics).
Pillar: {decision_trail}

Combines:
  1. oprim.vector_encode: find semantically similar KUs (vector locate)
  2. oprim.entity_graph_search: expand via graph relations (symbolic ontology)
  3. Check epistemic_status on retrieved nodes (filter/rank by grade)
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import ConfigDict

from omodul._base_config import BaseConfig
from oprim import entity_graph_search, vector_encode

_enabled_pillars: set[str] = {"decision_trail"}

GRADE_ORDER = ["proven", "high", "moderate", "low", "unverified"]


class GraphRAGQueryConfig(BaseConfig):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _omodul_name: ClassVar[str] = "graphrag_query"
    _omodul_version: ClassVar[str] = "1.0.0"
    backend: Any = None
    top_k: int = 10
    min_grade: str = "unverified"  # minimum epistemic grade to include in results


def graphrag_query(
    config,
    input_data: dict,
    output_dir: str | None = None,
    *,
    on_step: Callable | None = None,
) -> dict:
    """GraphRAG-style retrieval: vector locate + graph expand + epistemic filter.

    input_data: {"query": str, "seed_ids": list[str] | None}
    Returns: findings={results: list[{ku_id, grade, natural_text, score}]}, standard dict
    """
    if isinstance(config, dict):
        config = GraphRAGQueryConfig(**config) if config else GraphRAGQueryConfig()

    trail = []
    findings = None
    status = "failed"
    error = None

    try:
        backend = config.backend
        if backend is None:
            raise ValueError("backend required")

        query_text = input_data.get("query", "")
        seed_ids = input_data.get("seed_ids") or []

        # Step 1: Get all KU nodes from backend
        if hasattr(backend, "_nodes"):
            all_nodes = {nid: backend.get_node(nid) for nid in backend._nodes}
        else:
            all_nodes = {}

        # Step 2: Vector locate — encode query and find similar KUs
        trail.append({"step": "vector_encode", "query_len": len(query_text)})
        ku_texts = {
            nid: (node or {}).get("natural_text", nid) for nid, node in all_nodes.items() if node
        }

        vector_scores = {}
        if ku_texts and query_text:
            texts = [query_text] + list(ku_texts.values())
            vecs = vector_encode(texts=texts, normalize=True)
            q_vec = vecs[0]
            doc_vecs = vecs[1:]
            for i, (nid, _) in enumerate(ku_texts.items()):
                vector_scores[nid] = float(q_vec @ doc_vecs[i])
        trail[-1]["result"] = f"{len(vector_scores)} candidates"

        # Step 3: Graph expand from seed_ids
        trail.append({"step": "entity_graph_search", "seeds": len(seed_ids)})
        graph_hits = {}
        if seed_ids:
            results = entity_graph_search(
                seed_ids=seed_ids,
                list_edges=backend.list_edges,
                hops=2,
                top_k=config.top_k * 2,
            )
            for nid, score in results:
                graph_hits[nid] = score
        trail[-1]["result"] = f"{len(graph_hits)} graph hits"

        # Step 4: Merge scores and filter by epistemic grade
        grade_idx = {g: i for i, g in enumerate(GRADE_ORDER)}
        min_idx = grade_idx.get(config.min_grade, len(GRADE_ORDER) - 1)

        all_candidates = set(vector_scores) | set(graph_hits)
        results = []
        for nid in all_candidates:
            node = all_nodes.get(nid) or {}
            grade = (node.get("epistemic_status") or {}).get("grade", "unverified")
            if grade_idx.get(grade, len(GRADE_ORDER) - 1) > min_idx:
                continue  # below minimum grade
            combined_score = vector_scores.get(nid, 0.0) + graph_hits.get(nid, 0.0) * 0.5
            results.append(
                {
                    "ku_id": nid,
                    "grade": grade,
                    "natural_text": node.get("natural_text", ""),
                    "score": combined_score,
                }
            )

        results.sort(key=lambda x: -x["score"])
        findings = {"results": results[: config.top_k], "total_candidates": len(all_candidates)}
        status = "completed"

    except Exception as e:
        error = {"code": "ERR_GRAPHRAG", "message": str(e)}
        trail.append({"step": "abort", "error": error})
    finally:
        decision_trail = {
            "omodul": "graphrag_query",
            "enabled_pillars": sorted(_enabled_pillars),
            "status": status,
            "steps": trail,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if output_dir and findings is not None:
            os.makedirs(output_dir, exist_ok=True)
            with open(os.path.join(output_dir, "decision_trail.json"), "w", encoding="utf-8") as f:
                json.dump(decision_trail, f, ensure_ascii=False, indent=2)

    return {
        "findings": findings,
        "status": status,
        "error": error,
        "decision_trail": decision_trail,
        "report_path": None,
        "cost_usd": 0.0,
    }
