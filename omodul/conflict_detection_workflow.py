"""M-G1: conflict_detection_workflow — batch conflict detection omodul.

Pillars: fingerprint, decision_trail, cost
Fingerprint fields: corpus_id, batch_id

Composition: conflict_resolution (oskill K-G1).
Produces a conflict pair list + decision_trail recording which pairs were
checked and which conflicts were confirmed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

from obase.provider_registry import ProviderRegistry

from omodul._base import (
    BaseConfig, CostTracker, Trail, build_result, compute_fingerprint,
)


class ConflictDetectionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "conflict_detection_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set] = {"fingerprint", "decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set] = {"corpus_id", "batch_id"}

    corpus_id: str
    batch_id: str


async def conflict_detection_workflow(
    config: ConflictDetectionConfig,
    input_data: Any,   # ConflictDetectionInput (oprim._aii_graph_types)
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Detect conflicts between new and existing KUs.

    Composition: conflict_resolution (K-G1, oskill).
    Records decision_trail with pair counts and conflict details.
    """
    from oskill._conflict_resolution import conflict_resolution

    trail = Trail()
    cost = CostTracker()
    fingerprint = compute_fingerprint({
        "corpus_id": config.corpus_id,
        "batch_id": config.batch_id,
    })

    new_texts = list(getattr(input_data, "new_ku_texts", []))
    new_embeddings = list(getattr(input_data, "new_ku_embeddings", []))
    existing_texts = list(getattr(input_data, "existing_ku_texts", []))
    existing_embeddings = list(getattr(input_data, "existing_ku_embeddings", []))
    existing_ids = list(getattr(input_data, "existing_ku_ids", []))

    if not new_texts:
        return build_result(
            status="failed",
            error={"type": "ValueError", "message": "new_ku_texts is empty"},
            fingerprint=fingerprint,
            trail=trail,
            trail_path=None,
            cost_usd=0.0,
        )

    try:
        llm = ProviderRegistry.get().llm(config.llm_provider)

        trail.record(
            event="start",
            corpus_id=config.corpus_id,
            batch_id=config.batch_id,
            n_new=len(new_texts),
            n_existing=len(existing_texts),
            fingerprint=fingerprint,
        )
        _notify(on_step, "conflict_detection", "started")

        conflict_pairs = await conflict_resolution(
            new_ku_texts=new_texts,
            new_ku_embeddings=new_embeddings,
            existing_ku_texts=existing_texts,
            existing_ku_embeddings=existing_embeddings,
            existing_ku_ids=existing_ids,
            llm=llm,
        )

        pairs_as_dicts = [
            {
                "new_ku_idx": p.new_ku_idx,
                "existing_ku_id": p.existing_ku_id,
                "conflict_type": p.conflict_type,
                "description": p.description,
                "severity": p.severity,
                "grade": p.grade,
            }
            for p in conflict_pairs
        ]

        trail.record(
            event="detection_done",
            pairs_checked=len(new_texts) * len(existing_texts),
            conflicts_found=len(conflict_pairs),
            conflict_ids=[p.existing_ku_id for p in conflict_pairs],
        )
        _notify(on_step, "conflict_detection", "done")

        trail_path = trail.write(output_dir)

        return build_result(
            status="completed",
            error=None,
            fingerprint=fingerprint,
            trail=trail,
            trail_path=trail_path,
            cost_usd=cost.total_usd,
            conflict_pairs=pairs_as_dicts,
            conflicts_found=len(conflict_pairs),
            corpus_id=config.corpus_id,
            batch_id=config.batch_id,
        )

    except asyncio.CancelledError:
        trail.record(event="cancelled")
        trail.write(output_dir)
        raise

    except Exception as exc:
        trail.record(event="error", error_type=type(exc).__name__, message=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fingerprint,
            trail=trail,
            trail_path=None,
            cost_usd=cost.total_usd,
        )


def _notify(on_step: Any, step: str, state: str) -> None:
    if on_step is not None:
        try:
            on_step(step, state)
        except Exception:
            pass
