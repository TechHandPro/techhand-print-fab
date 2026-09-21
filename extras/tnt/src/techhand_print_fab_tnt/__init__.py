"""Optional ticket attach. Importing this package is not part of the core install.

The bridge shells out to the user-tnt CLI. It does not import TNT and it does
not start a printer.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from techhand_print_fab.policy import screen
from techhand_print_fab.results import failure, ok
from techhand_print_fab.store import StoreError, get_store, slugify

_ATTACH = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
_ATTACH_FILES = ("part.json", "model.scad", "model.py", "model.stl", "model.3mf")


def register(server: MCPServer) -> None:
    server.add_tool(fab_attach_to_ticket, annotations=_ATTACH)


def fab_attach_to_ticket(
    ticket_id: Annotated[int, Field(description="TNT ticket id to receive the files.", gt=0)],
    project_id: Annotated[str, Field(description="Local fab project id.")],
    part_name: Annotated[str, Field(description="Part name or slug.")],
    note: Annotated[str, Field(description="Short note stored with the handoff.")] = "",
) -> dict[str, Any]:
    """Hand local fab files to the user-tnt CLI.

    Enabled only when this extra package is installed and the server was
    started with TECHHAND_FAB_ENABLE_TNT=1. The core tool list does not include
    this tool. No printer job is started. user-tnt must be on PATH (or
    USER_TNT_COMMAND). Stdin is a JSON payload and the argv is `attach-fab`.
    """
    if isinstance(ticket_id, bool) or not isinstance(ticket_id, int) or ticket_id <= 0:
        return failure("ticket_id must be a positive integer")
    if len(note) > 4000:
        return failure("note is too long")
    blocked = screen(note, part_name)
    if blocked:
        return blocked
    try:
        store = get_store()
        if store.read_project(project_id) is None:
            return failure("unknown project id")
        slug = slugify(part_name)
        directory = store.part_dir(project_id, slug)
        record = store.read_part(project_id, slug)
    except StoreError as exc:
        return failure(str(exc))
    if record is None:
        return failure("unknown part")
    files: list[dict[str, Any]] = []
    for name in _ATTACH_FILES:
        path = directory / name
        if path.is_file():
            files.append({"name": name, "path": str(path.resolve()), "bytes": path.stat().st_size})
    if not files:
        return failure("part has no files to attach")
    payload = {
        "action": "attach_fab_artifact",
        "ticket_id": ticket_id,
        "project_id": project_id,
        "part_name": slug,
        "note": note,
        "files": files,
        "printer_dispatched": False,
    }
    try:
        completed = run_user_tnt(payload)
    except FileNotFoundError:
        return failure(
            "user-tnt was not found. Install it or set USER_TNT_COMMAND. Files were not attached."
        )
    except subprocess.TimeoutExpired:
        return failure("user-tnt attach-fab timed out. Files were not confirmed attached.")
    stdout = completed.stdout.decode("utf-8", errors="replace")[:2000]
    stderr = completed.stderr.decode("utf-8", errors="replace")[:2000]
    if completed.returncode != 0:
        return failure(
            f"user-tnt attach-fab exited {completed.returncode}. The ticket was not confirmed.",
            returncode=completed.returncode,
            stderr=stderr,
        )
    return ok(
        "user-tnt attach-fab exited 0. This bridge did not start a printer and did not "
        "verify the ticket beyond that exit code.",
        ticket_id=ticket_id,
        project_id=project_id,
        part_name=slug,
        files=files,
        bridge_stdout=stdout,
    )


def run_user_tnt(payload: dict[str, Any]) -> subprocess.CompletedProcess[bytes]:
    command = shlex.split(os.environ.get("USER_TNT_COMMAND", "user-tnt"))
    if not command:
        raise FileNotFoundError("USER_TNT_COMMAND is empty")
    return subprocess.run(
        [*command, "attach-fab"],
        input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
