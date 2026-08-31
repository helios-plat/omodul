"""MCP specialization of the canonical governed tool transaction."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from omodul.governed_tool_transaction import (
    GovernedToolConfig,
    GovernedToolInput,
    governed_tool_transaction,
)

GovernedMcpConfig = GovernedToolConfig
GovernedMcpInput = GovernedToolInput


async def governed_mcp_transaction(
    config: GovernedToolConfig,
    input_data: GovernedToolInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Run an MCP request through the same Tool/Action Gateway contract."""
    if input_data.request.kind != "mcp":
        return {
            "status": "failed",
            "error": {
                "type": "InvalidToolKind",
                "message": "MCP transaction requires kind='mcp'",
            },
            "executed": False,
        }
    return await governed_tool_transaction(config, input_data, output_dir, on_step=on_step)


__all__ = [
    "GovernedMcpConfig",
    "GovernedMcpInput",
    "governed_mcp_transaction",
]
