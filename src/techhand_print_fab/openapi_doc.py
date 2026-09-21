"""OpenAPI view of the MCP tools. These paths are the tool contract, not a second HTTP API."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from mcp import Client
from mcp.server import MCPServer

from techhand_print_fab import __version__

_RESULT = {
    "type": "object",
    "required": ["ok", "dry_fire", "printer_dispatched", "message"],
    "properties": {
        "ok": {"type": "boolean"},
        "refused": {"type": "boolean"},
        "dry_fire": {"type": "boolean"},
        "printer_dispatched": {"type": "boolean"},
        "mode": {"type": "string"},
        "message": {"type": "string"},
        "note": {"type": "string"},
    },
}


def build_openapi(server: MCPServer) -> dict[str, Any]:
    tools = asyncio.run(_list_tools(server))
    paths: dict[str, Any] = {}
    for tool in tools:
        paths[f"/tools/{tool.name}"] = {
            "post": {
                "operationId": tool.name,
                "summary": tool.name,
                "description": inspect.cleandoc(tool.description or ""),
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": tool.input_schema}},
                },
                "responses": {
                    "200": {
                        "description": "MCP tool result. printer_dispatched is true only after a sliced job is accepted.",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ToolResult"}}},
                    }
                },
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "techhand-print-fab MCP tools",
            "version": __version__,
            "description": (
                "Tool contract for the techhand-print-fab MCP server. "
                "Clients speak MCP on stdio or Streamable HTTP at /mcp. "
                "These paths are not served as REST. "
                "Design tools stay dry-fire. fab_slice shells out to a local OrcaSlicer or Bambu Studio CLI. "
                "It flattens installed X1 Carbon 0.4 mm presets when they still use inherits, and it "
                "locks the standing textured bed to plate_id textured_pei "
                "(Orca curr_bed_type Textured PEI Plate) and rejects a cool_plate tag. "
                "It does not ship vendor profiles and does not call the Bambu cloud. "
                "fab_bambu_push_3mf dispatches a sliced .gcode.3mf "
                "only when dry_run is false, confirm is true, and BAMBU_PRINT_ENABLED=1. "
                "printer_dispatched is true only after the job is accepted. "
                "LAN Developer Mode is the print path. Credentials are environment variables, never this file."
            ),
        },
        "paths": dict(sorted(paths.items())),
        "components": {"schemas": {"ToolResult": _RESULT}},
    }


async def _list_tools(server: MCPServer) -> list[Any]:
    async with Client(server) as client:
        listed = await client.list_tools()
        return list(listed.tools)
