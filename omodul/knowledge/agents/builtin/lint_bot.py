"""LintBotAgent — weekly health check: orphan substrates, broken refs, etc."""
from __future__ import annotations

import time

from oskill.knowledge import lint

from omodul.knowledge.agents.base import Agent, AgentContext, AgentResult, AgentStep
from omodul.knowledge.agents.registry import register_agent


@register_agent
class LintBotAgent(Agent):
    name = "lint_bot"
    description = "Weekly health check: orphan substrates, broken refs, missing embeddings."
    allowed_tools = [
        "oskill.knowledge.lint",
        "oprim.push.dispatcher",
    ]

    async def run(self, params: dict, context: AgentContext) -> AgentResult:
        trace: list[AgentStep] = []

        # 1. Run lint
        t0 = time.monotonic()
        try:
            issues = await lint(scope="all")
        except Exception as exc:
            issues = []
            trace.append(
                AgentStep(
                    step_num=1,
                    tool_name="lint",
                    tool_input={"scope": "all"},
                    error=str(exc),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
            )
            return AgentResult(
                success=False,
                output={"issues_count": 0, "error": str(exc)},
                trace=trace,
                citations=[],
                error=str(exc),
            )

        trace.append(
            AgentStep(
                step_num=1,
                tool_name="lint",
                tool_input={"scope": "all"},
                tool_output={"issues_count": len(issues)},
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        )

        issues_data = [
            {
                "type": getattr(i, "type", str(i)),
                "description": getattr(i, "description", str(i)),
                "severity": getattr(i, "severity", "warning"),
            }
            for i in issues
        ]

        # 2. Push if there are issues
        if issues:
            summary = "\n".join(
                f"- {i['type']}: {i['description']}" for i in issues_data[:5]
            )
            t1 = time.monotonic()
            push_sent = False
            try:
                dispatcher = self._get_dispatcher()
                if dispatcher:
                    await dispatcher.push(
                        user_id=context.user_id,
                        title=f"Weekly Lint — {len(issues)} issues found",
                        body=summary[:500],
                    )
                    push_sent = True
            except Exception:
                pass
            trace.append(
                AgentStep(
                    step_num=2,
                    tool_name="push.dispatcher",
                    tool_input={"user_id": context.user_id},
                    tool_output={"sent": push_sent, "issues": len(issues)},
                    duration_ms=int((time.monotonic() - t1) * 1000),
                )
            )

        return AgentResult(
            success=True,
            output={"issues_count": len(issues), "issues": issues_data},
            trace=trace,
            citations=[],
        )

    def _get_dispatcher(self):
        return None  # push requires configured channels; best-effort
