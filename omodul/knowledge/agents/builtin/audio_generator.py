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
        return AgentResult(
            success=False,
            output={
                "status": "failed",
                "error": "TTS unavailable v1.0: F5-TTS upstream image broken, v1.1+ evaluate"
            },
            trace=[],
            citations=[],
            cost_usd=0.0
        )
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
