"""AudioGeneratorAgent — batch TTS narration for pinned substrates."""
from __future__ import annotations

import time
from typing import Any

from oskill.knowledge import generate_audio_narration

from omodul.knowledge.agents.base import Agent, AgentContext, AgentResult, AgentStep
from omodul.knowledge.agents.registry import register_agent


@register_agent
class AudioGeneratorAgent(Agent):
    """Generate audio narration for substrates marked is_pinned or tagged 'audio'.

    Runs generate_audio_narration per substrate, respects GpuLock internally.
    Suited for nightly scheduled runs (cron 0 3 * * *).
    """

    name = "audio_generator"
    description = "Generate audio narration for pinned / audio-tagged substrates."
    allowed_tools = [
        "oskill.knowledge.generate_audio_narration",
        "oskill.knowledge.hybrid_search",
    ]
    timeout_seconds = 3600

    async def run(self, params: dict, context: AgentContext) -> AgentResult:
        max_substrates: int = params.get("max_substrates", 5)
        voice: str = params.get("voice", "default")
        speed: float = float(params.get("speed", 1.0))

        substrate_ids = _find_audio_candidates(context.user_id, max_substrates)
        trace: list[AgentStep] = []
        generated = 0
        skipped = 0
        failed = 0

        for substrate_id in substrate_ids:
            t0 = time.monotonic()
            try:
                result = await generate_audio_narration(
                    substrate_id=substrate_id,
                    voice=voice,
                    speed=speed,
                )
                elapsed = int((time.monotonic() - t0) * 1000)
                generated += 1
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="oskill.knowledge.generate_audio_narration",
                        tool_input={"substrate_id": substrate_id, "voice": voice},
                        tool_output={
                            "asset_id": result.audio_asset_id,
                            "audio_path": result.audio_path,
                            "duration_seconds": result.duration_seconds,
                            "chunk_count": result.chunk_count,
                        },
                        duration_ms=elapsed,
                    )
                )
            except ValueError:
                # No text content — skip silently
                skipped += 1
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="oskill.knowledge.generate_audio_narration",
                        tool_input={"substrate_id": substrate_id},
                        tool_output={"skipped": True, "reason": "no_text_content"},
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                )
            except Exception as exc:
                failed += 1
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="oskill.knowledge.generate_audio_narration",
                        tool_input={"substrate_id": substrate_id},
                        error=str(exc),
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                )

        return AgentResult(
            success=(failed == 0),
            output={
                "candidates_found": len(substrate_ids),
                "generated": generated,
                "skipped": skipped,
                "failed": failed,
            },
            trace=trace,
            citations=[],
            cost_usd=0.0,
        )


def _find_audio_candidates(user_id: str, limit: int) -> list[str]:
    """Return substrate IDs that are pinned or tagged 'audio', without existing audio_assets."""
    try:
        from oprim.meta_db import open_meta_db
        from oskill.knowledge._context import meta_db_path

        db_path = meta_db_path()
        if not db_path.exists():
            return []

        db = open_meta_db(db_path)
        # Pinned substrates that don't yet have an audio_asset
        rows = db.fetchall(
            """
            SELECT s.id FROM substrate s
            LEFT JOIN audio_assets aa ON aa.substrate_id = s.id
            WHERE s.is_pinned = TRUE
              AND aa.id IS NULL
              AND (s.mime LIKE 'text/%' OR s.mime IS NULL)
            ORDER BY s.created_at DESC
            LIMIT ?
            """,
            [limit],
        )
        db.close()
        return [r[0] for r in rows]
    except Exception:
        return []
