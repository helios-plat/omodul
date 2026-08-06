"""omodul.cindy_mcp_server — Cindy-style MCP server with progressive discovery.

In-process MCP server exposing tool registries per domain (memory, scheduler,
contacts, tools). Entry tools: ``list_tools(category)`` + ``call_tool({name, args})``
for progressive discovery — agent first lists categories, then drills into
specific tools, then calls them. Mirrors Cindy's ``cindy_memoryMcpServer`` pattern.

3O element: ``omodul.cindy_mcp_server`` (``CindyMcpServer`` class).
"""

from __future__ import annotations

import json
from typing import Any, Callable


class CindyMcpServer:
    """Progressive-discovery MCP server with per-domain tool registries.

    Usage::

        srv = CindyMcpServer(name="cindy_memory")
        srv.register("read", "Read a memory entry by id.", lambda id: store.read(id))
        tools = srv.list_tools()
        result = srv.call_tool({"name": "read", "args": {"id": "module/auth"}})
    """

    def __init__(self, name: str = "cindy_mcp") -> None:
        self.name = name
        self._tools: dict[str, dict[str, Any]] = {}  # name → {desc, func, category, schema}
        self._categories: dict[str, list[str]] = {}

    # -- registration -------------------------------------------------------
    def register(
        self,
        name: str,
        description: str,
        func: Callable,
        category: str = "general",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        """Register a tool in the MCP server."""
        self._tools[name] = {
            "name": name, "description": description, "func": func,
            "category": category, "inputSchema": input_schema or {"type": "object", "properties": {}},
        }
        self._categories.setdefault(category, []).append(name)

    def list_tools(self, category: str | None = None) -> dict[str, Any]:
        """Progressive discovery: ``list_tools()`` → categories; ``list_tools(category)`` → tool details."""
        if category is None:
            cats = {}
            for cat, names in self._categories.items():
                cats[cat] = {"count": len(names), "description": f"{cat} tools ({len(names)} available)"}
            return {"categories": cats, "hint": "Use list_tools(category) to drill into a category"}

        if category not in self._categories:
            return {"error": f"unknown category: {category}", "available_categories": sorted(self._categories)}

        tools = []
        for name in self._categories[category]:
            t = self._tools[name]
            tools.append({"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]})
        return {"category": category, "tools": tools, "count": len(tools)}

    def call_tool(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a registered tool by name."""
        t = self._tools.get(name)
        if t is None:
            return {"error": f"tool not found: {name}", "code": "TOOL_NOT_FOUND", "hint": "Use list_tools() to discover available tools"}
        args = args or {}
        try:
            result = t["func"](**args)
            return {"result": result, "tool": name}
        except TypeError as exc:
            return {"error": f"invalid args: {exc}", "code": "INVALID_ARGS", "expected_schema": t["inputSchema"]}
        except Exception as exc:
            return {"error": str(exc), "code": "TOOL_ERROR", "tool": name}

    def categories(self) -> list[str]:
        return sorted(self._categories)


# ---------------------------------------------------------------------------
# pre-built servers (mirror Cindy's domain-specific McpServers)
# ---------------------------------------------------------------------------


def build_memory_mcp_server(memory_store: Any | None = None) -> CindyMcpServer:
    """Build a ``cindy_memory`` MCP server over a knowledge/memory store."""
    srv = CindyMcpServer(name="cindy_memory")

    store = memory_store
    if store is None:
        try:
            from obase.knowledge_store import KnowledgeStore
            store = KnowledgeStore()
        except Exception:
            store = None

    if store is not None:
        srv.register("memory_read", "Read a memory entry by id.", lambda id: store.read(id) if hasattr(store, "read") else None, "read", {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]})
        srv.register("memory_list", "List memory entries (optionally by type).", lambda type=None: store.list_all(type) if hasattr(store, "list_all") else [], "read", {"type": "object", "properties": {"type": {"type": "string"}}})
        srv.register("memory_write", "Write/update a memory entry.", lambda id, type, body, **fm: str(store.write(id, type, body, **fm)) if hasattr(store, "write") else "unavailable", "write", {"type": "object", "properties": {"id": {"type": "string"}, "type": {"type": "string"}, "body": {"type": "string"}}, "required": ["id", "type", "body"]})
        srv.register("memory_delete", "Delete a memory entry.", lambda id: store.delete(id) if hasattr(store, "delete") else False, "maintain", {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]})
        srv.register("memory_search", "Search memory entries by keyword.", lambda query: [d for d in (store.list_all() if hasattr(store, "list_all") else []) if query.lower() in json.dumps(d).lower()], "search", {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})

    srv.register("memory_consolidate", "Consolidate stale memory entries (mark fresh).", lambda: _consolidate(store), "maintain")
    srv.register("memory_review", "Review stale memory entries.", lambda: [d["frontmatter"] for d in (store.list_stale() if hasattr(store, "list_stale") else [])], "maintain")
    return srv


def build_scheduler_mcp_server(scheduler: Any | None = None) -> CindyMcpServer:
    """Build a ``cindy_scheduler`` MCP server over a recurring scheduler."""
    srv = CindyMcpServer(name="cindy_scheduler")
    sched = scheduler
    if sched is None:
        try:
            from oskill.recurring_scheduler import RecurringScheduler
            sched = RecurringScheduler()
        except Exception:
            sched = None
    if sched is not None:
        srv.register("scheduler_list", "List all schedules.", lambda: [{"id": s.id, "name": s.name, "enabled": s.enabled, "phase": s.phase, "run_count": s.run_count} for s in sched.list_all()], "read", {"type": "object", "properties": {}})
        srv.register("scheduler_create", "Create a new schedule.", lambda id, name, prompt="", cron="", interval_ms=0: {"id": sched.create(id, name, prompt, cron, interval_ms).id}, "write", {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "prompt": {"type": "string"}, "cron": {"type": "string"}, "interval_ms": {"type": "integer"}}, "required": ["id", "name"]})
        srv.register("scheduler_toggle", "Enable/disable a schedule.", lambda id, enabled=True: {"ok": sched.update(id, enabled=enabled) is not None}, "write", {"type": "object", "properties": {"id": {"type": "string"}, "enabled": {"type": "boolean"}}, "required": ["id"]})
    return srv


def _consolidate(store: Any) -> dict[str, Any]:
    if store is None or not hasattr(store, "list_stale"):
        return {"consolidated": 0}
    stale = store.list_stale()
    for doc in stale:
        store.mark_fresh(doc["frontmatter"]["id"])
    return {"consolidated": len(stale)}
