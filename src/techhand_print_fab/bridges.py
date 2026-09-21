"""Optional bridges. The core server does not import a TNT client.

Ticket attach loads only through the techhand_print_fab.bridges entry-point
group, and only when the caller sets TECHHAND_FAB_ENABLE_TNT=1 or passes
enable_tnt=True. Installing the core package does not install that entry point.
"""

from __future__ import annotations

import os
from importlib.metadata import entry_points

from mcp.server import MCPServer

_ENTRY_GROUP = "techhand_print_fab.bridges"
_ENTRY_NAME = "tnt"


class BridgeNotInstalled(RuntimeError):
    """The TNT extra was requested and is not installed."""


def tnt_requested(flag: bool | None) -> bool:
    if flag is not None:
        return flag
    return os.environ.get("TECHHAND_FAB_ENABLE_TNT", "").strip() == "1"


def load_tnt_bridge(server: MCPServer) -> None:
    matches = [item for item in entry_points(group=_ENTRY_GROUP) if item.name == _ENTRY_NAME]
    if not matches:
        raise BridgeNotInstalled(
            "TECHHAND_FAB_ENABLE_TNT is set, but the optional techhand-print-fab-tnt "
            "package is not installed. The default server stays TNT-free. Install "
            "extras/tnt or unset the flag."
        )
    register = matches[0].load()
    register(server)
