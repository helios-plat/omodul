"""KnowledgeCuratorAgent — process inbox files: classify, dedup, ingest."""
from __future__ import annotations

import time
from pathlib import Path

from oskill.knowledge import classify_inbox_file, detect_duplicate_substrate, ingest_substrate

from omodul.knowledge.agents.base import Agent, AgentContext, AgentResult, AgentStep
from omodul.knowledge.agents.registry import register_agent


@register_agent
class KnowledgeCuratorAgent(Agent):
    name = "knowledge_curator"
    description = "Process inbox files: classify, deduplicate, ingest as substrate."
    allowed_tools = [
        "oskill.knowledge.classify_inbox_file",
        "oskill.knowledge.detect_duplicate_substrate",
        "oskill.knowledge.ingest_substrate",
        "oskill.knowledge.generate_derivative",
    ]

    async def run(self, params: dict, context: AgentContext) -> AgentResult:
        inbox_dir = Path(params.get("inbox_dir", "~/.stratum/inbox")).expanduser()
        trace: list[AgentStep] = []
        ingested = 0
        skipped = 0
        failed = 0

        files = [f for f in inbox_dir.glob("*") if f.is_file()] if inbox_dir.exists() else []

        for file_path in files:
            # classify
            try:
                t0 = time.monotonic()
                classification = classify_inbox_file(str(file_path))
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="classify_inbox_file",
                        tool_input={"file": str(file_path)},
                        tool_output={"medium": classification.medium},
                        duration_ms=int((time.monotonic() - t0) * 1000),
                    )
                )
            except Exception as exc:
                failed += 1
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="classify_inbox_file",
                        tool_input={"file": str(file_path)},
                        error=str(exc),
                    )
                )
                continue

            # dedup
            try:
                t0 = time.monotonic()
                dup = await detect_duplicate_substrate(file_path, classification)
                if dup:
                    skipped += 1
                    trace.append(
                        AgentStep(
                            step_num=len(trace) + 1,
                            tool_name="detect_duplicate_substrate",
                            tool_input={"file": str(file_path)},
                            tool_output={"duplicate_of": dup.substrate_id if hasattr(dup, "substrate_id") else str(dup)},
                            duration_ms=int((time.monotonic() - t0) * 1000),
                        )
                    )
                    continue
            except Exception as exc:
                # dedup failure is non-fatal — proceed with ingest
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="detect_duplicate_substrate",
                        tool_input={"file": str(file_path)},
                        error=str(exc),
                    )
                )

            # ingest
            try:
                t0 = time.monotonic()
                substrate = await ingest_substrate(
                    user_id=context.user_id,
                    file_path=file_path,
                    classification=classification,
                )
                ingested += 1
                trace.append(
                    AgentStep(
                        step_num=len(trace) + 1,
                        tool_name="ingest_substrate",
                        tool_input={"file": str(file_path)},
                        tool_output={"substrate_id": substrate.substrate_id},
                        duration_ms=int((time.monotonic() - t0) * 1000),
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
