"""MCP tools that discover a Bambu X1 Carbon and queue a sliced print."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from techhand_print_fab.bambu_config import BambuError, load_config, scrub_obj, secret_values
from techhand_print_fab.bambu_discover import discover_printers
from techhand_print_fab.print_job import queue_print
from techhand_print_fab.results import print_result

_DISCOVER = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
_QUEUE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)


def register_print_tools(server: MCPServer) -> None:
    server.add_tool(fab_discover_printers, annotations=_DISCOVER)
    server.add_tool(fab_queue_print, annotations=_QUEUE)


def fab_discover_printers(
    ssdp: Annotated[
        bool,
        Field(
            description=(
                "Send an SSDP M-SEARCH on UDP 2021 and 1990. "
                "Default false. BAMBU_DISCOVER_SSDP=1 also turns the scan on."
            )
        ),
    ] = False,
    transport: Annotated[
        str,
        Field(description="lan, farm, or both. Empty uses whichever of LAN and Farm Manager is configured."),
    ] = "",
) -> dict[str, Any]:
    """Find a Bambu printer on the LAN or on a configured Farm Manager server.

    Does not upload a file and does not start a print. A configured BAMBU_LAN_HOST
    is probed with the printer TCP 3000 detect frame. LAN Developer Mode is the
    print path this server implements. Farm Manager is optional. The cloud API is not used.
    """
    try:
        return discover_printers(ssdp=ssdp, transport=transport)
    except BambuError as exc:
        payload = print_result(ok=False, message=str(exc), printer_dispatched=False, mode="print_error")
        try:
            secrets = secret_values(load_config())
        except BambuError:
            secrets = ()
        scrubbed = scrub_obj(payload, secrets)
        if not isinstance(scrubbed, dict):
            return payload
        return scrubbed


def fab_queue_print(
    project_id: Annotated[str, Field(description="Project id from fab_create_project. Pair with part_name.")] = "",
    part_name: Annotated[str, Field(description="Part name or slug. Pair with project_id.")] = "",
    file_path: Annotated[
        str,
        Field(
            description=(
                "Optional .stl, .3mf, or sliced .gcode.3mf under the part directory, "
                "the project directory, or FAB_EXPORT_ROOTS. Empty uses model.gcode.3mf, "
                "then model.3mf, then model.stl."
            )
        ),
    ] = "",
    material: Annotated[
        str,
        Field(description="PETG, ASA, TPU, PA, or PA-CF. Empty uses the part material. Notes are not a slicer profile."),
    ] = "",
    intent: Annotated[str, Field(description="What this original training-tool or fab part is. Weapon-part requests are refused.")] = "",
    confirm: Annotated[
        bool,
        Field(description="True sends a sliced job. False returns a plan. Also requires BAMBU_PRINT_ENABLED=1."),
    ] = False,
    transport: Annotated[
        str,
        Field(description="lan or farm. Empty uses LAN when BAMBU_LAN_HOST is set, otherwise Farm Manager."),
    ] = "",
    device_id: Annotated[
        str,
        Field(description="Farm Manager dev_id when more than one printer is listed. Ignored on LAN."),
    ] = "",
    plate: Annotated[int, Field(description="Plate number inside the sliced 3MF. Default 1.", ge=1, le=16)] = 1,
    use_ams: Annotated[bool, Field(description="Ask the printer to use the AMS. Default false uses the external spool.")] = False,
    ams_slot: Annotated[
        int | None,
        Field(description="AMS tray index 0-15 when use_ams is true. Null lets the printer or farm match."),
    ] = None,
    bed_leveling: Annotated[bool, Field(description="Run bed leveling with the job.")] = True,
    flow_cali: Annotated[bool, Field(description="Run flow calibration with the job.")] = True,
    vibration_cali: Annotated[bool, Field(description="Run vibration calibration. Meaningful on the X1 Carbon.")] = True,
    timelapse: Annotated[bool, Field(description="Record a timelapse.")] = False,
    bed_type: Annotated[
        str,
        Field(description="auto, hot_plate, textured_plate, cool_plate, or engineering_plate."),
    ] = "auto",
    queue_only: Annotated[
        bool,
        Field(
            description=(
                "Farm Manager only: send task_print_model 0 so the task is queued. "
                "On LAN this refuses to upload, because project_file starts the job."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Queue a sliced .gcode.3mf to a Bambu X1 Carbon, or hand an unsliced STL/3MF to Bambu Studio.

    LAN Developer Mode (MQTT 8883 and implicit FTPS 990) is the print path. Farm Manager
    is used when transport is farm, or when BAMBU_FARM_URL is set and no LAN host is configured.
    Geometry-only STL and 3MF files are not sent. The tool writes a Studio handoff and X1 Carbon
    profile notes. confirm true and BAMBU_PRINT_ENABLED=1 are both required before a job is sent.
    Firearm and other weapon-part requests are refused. Training-tool and general fab jobs are in scope.
    Credentials come from the process environment, not from the repo.
    """
    return queue_print(
        project_id=project_id,
        part_name=part_name,
        file_path=file_path,
        material=material,
        intent=intent,
        confirm=confirm,
        transport=transport,
        device_id=device_id,
        plate=plate,
        use_ams=use_ams,
        ams_slot=ams_slot,
        bed_leveling=bed_leveling,
        flow_cali=flow_cali,
        vibration_cali=vibration_cali,
        timelapse=timelapse,
        bed_type=bed_type,
        queue_only=queue_only,
    )
