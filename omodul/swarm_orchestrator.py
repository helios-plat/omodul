"""
)omodul.swarm_orchestrator — multi-agent Map-Reduce swarm orchestration.

3O layer: omodul (transaction orchestration).
For large multi-component deliverables a single LLM suffers context loss /
hallucination / token blowups. The swarm decomposes (Map), runs role-masked
sub-agents concurrently (Execute via asyncio.gather), then the master LLM
reviews consistency, resolves conflicts and synthesizes (Reduce).

Lifecycle events are published to obase.event_bus ("swarm_notify") — the host
bridges them to its notification channel (SSE). LLM calls are host-injected
(veya assembles veya.llm.llm_call); no main-library dependency on the host.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from obase.event_bus import EventBus, default_event_bus
from oskill.sub_agent import SubAgent

_log = logging.getLogger(__name__)

MASTER_SYNTHESIS_PROMPT = (
    """You are the Master Architect. Your swarm has completed their individual sub-tasks for
    the following goal:
[GOAL]: {overarching_goal}

[SWARM OUTPUTS]:
{synthesis_payload}

YOUR MISSION:
1. Review all outputs for consistency (e.g., did the frontend use the same API
   routes the backend created?).
2. Resolve any conflicts.
3. Synthesize this into a cohesive final output (e.g., a complete file
   structure, or a combined Veya Artifact).
"""
)


class SwarmOrchestrator:
    """Map-Reduce swarm scheduler: decompose -> concurrent execute -> synthesize."""

    def __init__(
        self,
        llm_caller: Callable | None = None,
        *,
        sub_agent_factory: Callable | None = None,
        event_bus: EventBus | None = None,
        notify_delay: float = 0.5,
    ):
        """
        Args:
            llm_caller: Host-injected master LLM (async (messages, **kwargs) -> dict).
            sub_agent_factory: Worker factory (role, context) -> SubAgent-like.
                Defaults to oskill.sub_agent.SubAgent bound to llm_caller.
            event_bus: Lifecycle notification bus (default obase default bus).
            notify_delay: Stagger between agent-start notifications (UI effect).
        """
        self._llm_caller = llm_caller
        self.event_bus = event_bus or default_event_bus
        self.notify_delay = notify_delay
        self._sub_agent_factory = sub_agent_factory or (
            lambda role, context: SubAgent(role=role, context=context, llm_caller=llm_caller)
        )

    def _notify(self, level: str, title: str, content: str) -> None:
        self.event_bus.publish(
            "swarm_notify",
            {"level": level, "title": title, "content": content},
        )

    async def run_swarm(self, overarching_goal: str, sub_tasks: list[dict]) -> str:
        """Full Map-Reduce flow: broadcast -> concurrent execute -> synthesize."""
        if not sub_tasks:
            return "Swarm aborted: no sub-tasks provided."

        # 1. [Map] broadcast: swarm begins
        self._notify(
            "INFO",
            "🐝 Swarm Initiated",
            f"Deploying {len(sub_tasks)} specialized sub-agents concurrently...",
        )

        # 2. [Execute] instantiate all workers and create async tasks
        tasks = []
        for index, task_def in enumerate(sub_tasks):
            role = task_def["role"]
            instruction = task_def["instruction"]
            agent = self._sub_agent_factory(role=role, context=overarching_goal)
            tasks.append(self._run_and_notify(agent, instruction, index))

        # 3. True concurrency (asyncio.gather — parallel LLM API calls)
        results = await asyncio.gather(*tasks)

        # 4. Concatenate all worker outputs
        synthesis_payload = ""
        for i, res in enumerate(results):
            synthesis_payload += f"--- [Output from {sub_tasks[i]['role']}] ---\n{res}\n\n"

        self._notify(
            "INFO",
            "🧩 Swarm Execution Complete",
            "All sub-agents have finished. Master is now synthesizing the final architecture...",
        )

        # 5. [Reduce] master synthesis: review consistency / resolve conflicts
        synthesis_prompt = MASTER_SYNTHESIS_PROMPT.format(
            overarching_goal=overarching_goal, synthesis_payload=synthesis_payload
        )
        final_response = await self._synthesize(synthesis_prompt, synthesis_payload)

        self._notify(
            "SUCCESS", "🚀 Swarm Mission Accomplished", "Final synthesis completed successfully."
        )
        return final_response

    async def _synthesize(self, synthesis_prompt: str, raw_outputs: str) -> str:
        if self._llm_caller is None:
            return (
                "[MASTER SYNTHESIS FAILED: llm_caller 未注入]\n\nRaw swarm outputs:\n" + raw_outputs
            )
        try:
            response = await self._llm_caller(
                [{"role": "user", "content": synthesis_prompt}],
                max_tokens=8192,
                timeout=300.0,
            )
            return ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        except Exception as exc:  # noqa: BLE001 — synthesis failure keeps raw outputs
            _log.error("swarm synthesis failed: %s", exc)
            return (
                f"[MASTER SYNTHESIS FAILED: {exc}]\n\n"
                "Raw swarm outputs (unmodified):\n"
                + raw_outputs
            )

    async def _run_and_notify(self, agent: SubAgent, instruction: str, index: int) -> str:
        """Wrap one worker's execution with lifecycle notifications."""
        await asyncio.sleep(index * self.notify_delay)  # stagger notifications (UI effect)
        self._notify("INFO", "Agent Assigned", f"[{agent.role}] started working on their task.")

        result = await agent.execute(instruction)

        self._notify("SUCCESS", "Task Completed", f"[{agent.role}] finished successfully.")
        return result
