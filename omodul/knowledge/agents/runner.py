"""AgentRunner — executes an agent with timeout tracking and trace persistence."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime

from oprim._logging import log

from .base import Agent, AgentContext, AgentResult
from .tracer import AgentTracer


class AgentRunner:
    """Runs an agent and records the complete trace to DuckDB."""

    def __init__(self, tracer: AgentTracer) -> None:
        self.tracer = tracer

    async def run(
        self,
        agent: Agent,
        user_id: str,
        params: dict,
    ) -> AgentResult:
        run_id = str(uuid.uuid4())
        started_at = datetime.utcnow()
        t0 = time.monotonic()

        self.tracer.create_run(
            run_id=run_id,
            user_id=user_id,
            agent_name=agent.name,
            params=params,
            started_at=started_at,
        )

        context = AgentContext(
            user_id=user_id,
            agent_run_id=run_id,
            invoked_at=started_at,
        )

        log.info(
            "agent_run_started",
            agent=agent.name,
            run_id=run_id,
            user_id=user_id,
        )

        try:
            result = await asyncio.wait_for(
                agent.run(params, context),
                timeout=agent.timeout_seconds,
            )
            result.elapsed_seconds = time.monotonic() - t0

            self.tracer.complete_run(
                run_id=run_id,
                status="completed",
                trace=result.trace,
                citations=result.citations,
                output=result.output,
                total_input_tokens=result.total_input_tokens,
                total_output_tokens=result.total_output_tokens,
                cost_usd=result.cost_usd,
                completed_at=datetime.utcnow(),
            )

            log.info(
                "agent_run_completed",
                agent=agent.name,
                run_id=run_id,
                elapsed=round(result.elapsed_seconds, 2),
                cost_usd=result.cost_usd,
            )
            return result

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            msg = f"Timeout after {agent.timeout_seconds}s"
            log.error("agent_run_timeout", agent=agent.name, run_id=run_id, elapsed=elapsed)
            self.tracer.complete_run(
                run_id=run_id,
                status="timeout",
                error_message=msg,
                completed_at=datetime.utcnow(),
            )
            raise

        except Exception as exc:
            elapsed = time.monotonic() - t0
            log.error(
                "agent_run_failed",
                agent=agent.name,
                run_id=run_id,
                elapsed=round(elapsed, 2),
                error=str(exc),
            )
            self.tracer.complete_run(
                run_id=run_id,
                status="failed",
                error_message=str(exc),
                completed_at=datetime.utcnow(),
            )
            raise
