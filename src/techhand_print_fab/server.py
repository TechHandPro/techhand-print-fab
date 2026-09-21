"""MCP server factory. stdio is the Cursor default; HTTP is opt-in."""

from __future__ import annotations

from mcp.server import MCPServer

from techhand_print_fab import __version__
from techhand_print_fab.bridges import load_tnt_bridge, tnt_requested
from techhand_print_fab.tools import register_core_tools

INSTRUCTIONS = """
Shareable parametric fab tools for original parts.
OpenSCAD is the primary model. A CadQuery script can be written next to it and is not executed here.
Refuse requests to make a 1:1 copy of a proprietary commercial product.
Exports are files on disk. Never claim a printer job ran. Say that mesh export is dry-fire training output.
Ticket attach is absent unless the separate TNT extra is installed and TECHHAND_FAB_ENABLE_TNT=1.
This server does not require TNT.
""".strip()


def create_server(*, enable_tnt: bool | None = None) -> MCPServer:
    server = MCPServer(
        name="techhand-print-fab",
        title="TechHand Print Fab",
        instructions=INSTRUCTIONS,
        version=__version__,
    )
    register_core_tools(server)
    if tnt_requested(enable_tnt):
        load_tnt_bridge(server)
    return server
