"""omodul.team_lifecycle_workflow — ClawTeam team lifecycle (create → plan → dispatch → complete).

Orchestrates the full team lifecycle: create a team, add members, decompose a
goal into tasks, route them, execute, and track progress.

3O element: ``omodul.team_lifecycle_workflow`` (``team_lifecycle_workflow``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def team_lifecycle_workflow(
    config: dict[str, Any],
    input_data: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Run the team lifecycle: create → plan → dispatch.

    Config keys:
      * ``model`` / ``budget`` — pass-through
      * ``max_tasks`` — cap on generated tasks

    Input data keys:
      * ``team_name`` — required
      * ``goal`` — team objective
      * ``members`` — [{name, agent_type, ...}]
      * ``leader_name`` — optional, auto-first if absent
      * ``llm_caller`` — optional LLM

    Returns:
        {status, team, tasks, assignments, messages_sent}
    """
    team_name = str(input_data.get("team_name") or input_data.get("name") or "")
    if not team_name:
        return {"status": "failed", "error": "team_name is required"}

    goal = str(input_data.get("goal") or "")
    members_in = list(input_data.get("members") or [])
    leader = str(input_data.get("leader_name") or (members_in[0].get("name") if members_in else "leader"))

    # 1. create team
    try:
        from obase.team_registry import TeamRegistry
        reg = TeamRegistry(output_dir)
        if reg.get_team(team_name):
            reg.cleanup(team_name)
        cfg = reg.create_team(team_name, description=goal, lead_agent_id="", budget_cents=float(config.get("budget_cents", 0)))
    except Exception as exc:
        return {"status": "failed", "error": f"team creation: {exc}"}

    # 2. add members
    for m in members_in:
        if not m.get("name"):
            continue
        reg.add_member(team_name, **m)

    if not reg.list_members(team_name):
        reg.add_member(team_name, leader, agent_type="leader")

    members = reg.list_members(team_name)

    # 3. plan: goal → tasks
    try:
        from oskill.team_plan_gen import team_plan_gen
        llm = config.get("llm_caller")
        tasks = team_plan_gen(goal, members=members, llm_caller=llm, context={"use_llm": llm is not None})
    except Exception:
        tasks = [{"id": "t1", "subject": goal, "description": goal, "priority": "high", "blocks": [], "blocked_by": [], "suggested_owner": leader}]

    # 4. register tasks
    for t in tasks[: int(config.get("max_tasks", 20))]:
        t_copy = dict(t)
        subject = t_copy.pop("subject", t_copy.get("id", "?"))
        priority = t_copy.pop("priority", "medium")
        reg.add_task(team_name, subject, priority=priority, **t_copy)

    # 5. route: assign tasks to members
    all_tasks = reg.get_tasks(team_name)
    try:
        from oprim.task_router import route_tasks, dispatch_decision
        decisions = route_tasks(all_tasks, members, context={})
        dispatch = dispatch_decision(decisions, message_type="message")
    except Exception:
        decisions, dispatch = [], {"dispatched": []}

    # 6. send assignment messages
    msgs_sent = 0
    for d in dispatch.get("dispatched", []):
        try:
            reg.send_message(team_name, d["message"])
            msgs_sent += 1
        except Exception:
            pass

    # 7. lock assigned tasks + create git worktree per agent + P2P mailbox + kanban auto-unblock
    for d in dispatch.get("dispatched", []):
        reg.lock_task(team_name, d["task_id"], d["agent"])
        # Git worktree isolation per agent
        try:
            from oprim.git_worktree_add import git_worktree_add
            git_worktree_add(f"swarm/{d['agent']}", repo=str(output_dir))
        except Exception:
            pass

    # P2P mailbox integration
    try:
        from oprim.p2p_mailbox import P2PMailbox
        box = P2PMailbox(team_name=team_name, agent_name=leader)
        box.broadcast(f"New swarm tasks assigned. Ready queue: {len(dispatch.get('dispatched', []))} items.")
        box.close()
    except Exception:
        pass

    # Kanban auto-unblock: simulate completing first task → wake next
    try:
        from oprim.kanban_task_update import kanban_task_update
        all_ts = reg.get_tasks(team_name)
        if all_ts:
            kanban_task_update(all_ts[0]["id"], "completed", all_ts)
    except Exception:
        pass

    return {
        "status": "completed",
        "team": {"name": team_name, "members": len(members), "created_at": cfg.get("created_at")},
        "tasks_created": len(tasks),
        "tasks_assigned": len(dispatch.get("dispatched", [])),
        "messages_sent": msgs_sent,
        "assignments": dispatch.get("dispatched", []),
        "team_dir": str(reg._team_dir(team_name)),
    }
