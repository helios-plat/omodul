"""KnowledgeCuratorAgent — process inbox files: ingest (classify + dedup + index)."""

from __future__ import annotations

import time
from pathlib import Path

from oskill.ingest_substrate import ingest_substrate

from omodul.knowledge.agents.base import Agent, AgentContext, AgentResult, AgentStep
from omodul.knowledge.agents.registry import register_agent


@register_agent
class KnowledgeCuratorAgent(Agent):
    name = "knowledge_curator"
    description = "Process inbox files: ingest as substrate (classify + dedup + index)."
    allowed_tools = [
        "oskill.knowledge.ingest_substrate",
    ]

    async def run(self, params: dict, context: AgentContext) -> AgentResult:
        inbox_dir = Path(params.get("inbox_dir", "~/.stratum/inbox")).expanduser()
        trace: list[AgentStep] = []
        ingested = 0
        skipped = 0
        failed = 0

        files = [f for f in inbox_dir.glob("*") if f.is_file()] if inbox_dir.exists() else []

        for file_path in files:
            # ingest_substrate handles classify + dedup + embed + index internally
            try:
                t0 = time.monotonic()
                result = await ingest_substrate(
                    path=file_path,
                    source={"user_id": context.user_id},
                )
                elapsed = int((time.monotonic() - t0) * 1000)
                if result.duplicate_of:
                    skipped += 1
                    trace.append(
                        AgentStep(
                            step_num=len(trace) + 1,
                            tool_name="ingest_substrate",
                            tool_input={"file": str(file_path)},
                            tool_output={"duplicate_of": result.duplicate_of},
                            duration_ms=elapsed,
                        )
                    )
                else:
                    ingested += 1
                    trace.append(
                        AgentStep(
                            step_num=len(trace) + 1,
                            tool_name="ingest_substrate",
                            tool_input={"file": str(file_path)},
                            tool_output={
                                "substrate_id": result.substrate_id,
                                "medium": result.medium,
                            },
                            duration_ms=elapsed,
                        )
                    )
            except Exception as exc:
                failed += 1
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="ingest_substrate",
                        tool_input={"file": str(file_path)},
                        error=str(exc),
                    )
                )

        return AgentResult(
            success=(failed == 0),
            output={
                "files_found": len(files),
                "ingested": ingested,
                "skipped": skipped,
                "failed": failed,
            },
            trace=trace,
            citations=[],
        )
