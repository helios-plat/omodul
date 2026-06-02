"""TranslationWorkerAgent — batch translate English substrates lacking zh-CN derivative."""

from __future__ import annotations

import time

from oskill.translate_substrate import translate_substrate

from omodul.knowledge.agents.base import Agent, AgentContext, AgentResult, AgentStep, Citation
from omodul.knowledge.agents.registry import register_agent


@register_agent
class TranslationWorkerAgent(Agent):
    name = "translation_worker"
    description = "Batch-translate English substrates that lack a Chinese translation derivative."
    allowed_tools = [
        "oskill.knowledge.translate_substrate",
    ]
    timeout_seconds = 3600

    async def run(self, params: dict, context: AgentContext) -> AgentResult:
        max_substrates = int(params.get("max_substrates", 5))
        target_lang = params.get("target_lang", "zh-CN")

        trace: list[AgentStep] = []
        citations: list[Citation] = []
        translated = 0
        total_cost = 0.0

        # 1. Find candidates
        t0 = time.monotonic()
        candidates = self._find_candidates(context.user_id, max_substrates)
        trace.append(
            AgentStep(
                step_num=1,
                tool_name="list_substrates_without_translation",
                tool_input={"max": max_substrates, "target_lang": target_lang},
                tool_output={"candidates": len(candidates)},
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        )

        # 2. Translate each
        for sub_id in candidates:
            t0 = time.monotonic()
            try:
                result = await translate_substrate(
                    substrate_id=sub_id,
                    target_lang=target_lang,
                    embed_translation=True,
                )
                derivative_id = result.derivative_id
                cost = result.cost_usd
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="translate_substrate",
                        tool_input={"substrate_id": sub_id, "target_lang": target_lang},
                        tool_output={
                            "derivative_id": derivative_id,
                            "cost_usd": cost,
                            "chunks": result.chunks_translated,
                        },
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                )
                citations.append(
                    Citation(
                        substrate_id=sub_id,
                        deep_link=f"stratum://substrate/{sub_id}",
                    )
                )
                translated += 1
                total_cost += cost
            except Exception as exc:
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="translate_substrate",
                        tool_input={"substrate_id": sub_id},
                        error=str(exc),
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                )

        return AgentResult(
            success=(translated > 0 or len(candidates) == 0),
            output={"translated": translated, "candidates": len(candidates)},
            trace=trace,
            citations=citations,
            cost_usd=total_cost,
        )

    def _find_candidates(self, user_id: str, limit: int) -> list[str]:
        """Return substrate IDs without a zh-CN translation derivative."""
        try:
            from oprim.meta_db import open_meta_db
            from oskill.knowledge._context import meta_db_path

            db = open_meta_db(meta_db_path())
            rows = db.fetchall(
                """
                SELECT s.id FROM substrate s
                WHERE NOT EXISTS (
                    SELECT 1 FROM derivative d
                    WHERE d.substrate_id = s.id
                      AND d.kind LIKE 'translation%zh%'
                )
                LIMIT ?
                """,
                [limit],
            )
            return [r[0] for r in rows]
        except Exception:
            return []
