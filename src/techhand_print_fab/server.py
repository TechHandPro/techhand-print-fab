"""MCP server factory. stdio is the Cursor default; HTTP is opt-in."""

from __future__ import annotations

from mcp.server import MCPServer

from techhand_print_fab import __version__
from techhand_print_fab.bridges import load_tnt_bridge, tnt_requested
from techhand_print_fab.print_tools import register_print_tools
from techhand_print_fab.profiles import DEFAULT_BED_TYPE, DEFAULT_MATERIAL, TARGET_NOZZLE_MM, TARGET_PRINTER
from techhand_print_fab.tools import register_core_tools

INSTRUCTIONS = f"""
Shareable parametric fab tools for original parts, plus an optional Bambu X1 Carbon print push.
OpenSCAD is the primary model. A CadQuery script can be written next to it and is not executed here.
Refuse requests to make a 1:1 copy of a proprietary commercial product.
Refuse firearm and other weapon-part print requests. Training-tool and general fab jobs are in scope.
Design exports are files on disk. Mesh export is dry-fire training output.
Standing defaults are {DEFAULT_MATERIAL}, bed {DEFAULT_BED_TYPE}, {TARGET_PRINTER}, {TARGET_NOZZLE_MM:.1f} mm nozzle, unless the part or the call sets another material or bed.
fab_slice writes a .gcode.3mf when OrcaSlicer or Bambu Studio's CLI and FAB_SLICER_PRESETS are available. It does not call the Bambu cloud.
fab_bambu_discover lists host, model, state, and AMS when the printer exposes it.
fab_bambu_status reads nozzle, bed, and job progress and does not queue a job.
fab_bambu_push_3mf slices an unsliced STL or 3MF on that path, then defaults to dry-run. A live push needs dry_run false, confirm true, and BAMBU_PRINT_ENABLED=1.
printer_dispatched is true only after the printer or Farm Manager accepts the job.
When the slicer CLI or presets are missing, an unsliced mesh is a Bambu Studio handoff and no printer job.
LAN Developer Mode is the print path. Farm Manager is optional. The cloud API is not used.
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
    register_print_tools(server)
    if tnt_requested(enable_tnt):
        load_tnt_bridge(server)
    return server
