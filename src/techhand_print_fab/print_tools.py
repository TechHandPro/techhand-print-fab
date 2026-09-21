"""MCP tools that discover a Bambu X1 Carbon and queue a sliced print."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from techhand_print_fab.bambu_config import BambuError, load_config, scrub_obj, secret_values
from techhand_print_fab.bambu_discover import discover_printers
from techhand_print_fab.print_job import queue_print, read_printer_status, slice_model
from techhand_print_fab.profiles import DEFAULT_BED_TYPE, DEFAULT_MATERIAL, material_list
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


_SLICE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def register_print_tools(server: MCPServer) -> None:
    server.add_tool(fab_bambu_discover, annotations=_DISCOVER)
    server.add_tool(fab_bambu_status, annotations=_DISCOVER)
    server.add_tool(fab_slice, annotations=_SLICE)
    server.add_tool(fab_bambu_push_3mf, annotations=_QUEUE)


def fab_bambu_discover(
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
    """List Bambu printers on the LAN or on a configured Farm Manager server.

    Each printer includes host, model, state, and ams. ams is null when the printer
    did not expose an AMS object. Does not upload a file and does not start a print.
    printer_dispatched stays false. A configured BAMBU_LAN_HOST is probed with the
    TCP 3000 detect frame. That probe does not need an access code, serial, farm
    token, or BAMBU_PRINT_ENABLED. When BAMBU_ACCESS_CODE and the serial are set, a
    read-only MQTT pushall fills state and AMS. LAN Developer Mode is the print path.
    Farm Manager is optional. The cloud API is not used.
    """
    try:
        payload = discover_printers(ssdp=ssdp, transport=transport)
    except BambuError as exc:
        payload = print_result(ok=False, message=str(exc), printer_dispatched=False, mode="print_error")
    return _scrubbed(payload)


def fab_bambu_status(
    transport: Annotated[
        str,
        Field(description="lan or farm. Empty uses LAN when BAMBU_LAN_HOST is set, otherwise Farm Manager."),
    ] = "",
    device_id: Annotated[
        str,
        Field(description="Farm Manager dev_id when more than one printer is listed. Ignored on LAN."),
    ] = "",
) -> dict[str, Any]:
    """Read nozzle temperature, bed temperature, and job progress.

    LAN uses a read-only MQTT pushall. Farm Manager reads the device report from
    GET /devices. This tool does not upload a file and does not queue a job.
    printer_dispatched stays false. Credentials come from the process environment.
    """
    return read_printer_status(transport=transport, device_id=device_id)


def fab_slice(
    project_id: Annotated[str, Field(description="Project id from fab_create_project. Pair with part_name.")] = "",
    part_name: Annotated[str, Field(description="Part name or slug. Pair with project_id.")] = "",
    file_path: Annotated[
        str,
        Field(
            description=(
                "Optional .stl or geometry .3mf under the part directory, the project directory, "
                "or FAB_EXPORT_ROOTS. Empty uses a newer mesh than model.gcode.3mf when one exists, "
                "otherwise model.gcode.3mf, model.3mf, or model.stl. A parametric part with no mesh "
                "is exported to model.stl first."
            )
        ),
    ] = "",
    material: Annotated[
        str,
        Field(
            description=(
                f"{material_list()}. Empty uses the part material, then {DEFAULT_MATERIAL}. "
                "Notes are not a slicer profile."
            )
        ),
    ] = "",
    intent: Annotated[str, Field(description="What this original training-tool or fab part is. Weapon-part requests are refused.")] = "",
    bed_type: Annotated[
        str,
        Field(
            description=(
                f"Empty uses {DEFAULT_BED_TYPE}. Also hot_plate, cool_plate, engineering_plate, or auto."
            )
        ),
    ] = "",
) -> dict[str, Any]:
    """Slice a mesh to .gcode.3mf with OrcaSlicer or Bambu Studio's CLI.

    Standing defaults are PLA, a textured plate, and a Bambu X1 Carbon with a
    0.4 mm nozzle, unless the part or this call sets a material or bed.
    The CLI runs when orca-slicer or bambu-studio is on PATH (or ORCA_SLICER_BIN /
    BAMBU_STUDIO_BIN). Full presets (no inherits) may already be in FAB_SLICER_PRESETS.
    If machine.json, process.json, or filament/<MATERIAL>.json is missing or still
    inherits, this tool flattens Bambu Lab X1 Carbon 0.4 nozzle, 0.20 mm Standard,
    and the filament from the installed slicer's resources/profiles
    (or FAB_SLICER_PROFILE_ROOT). The CLI does not expand inherits.
    Stock X1 Carbon profiles often keep Cool Plate. This tool writes
    curr_bed_type Textured PEI Plate into process.json and into the sliced 3MF
    metadata unless another bed is named. sliced_plate reports that plate name.
    This tool does not ship a Bambu or Orca vendor profile and does not call the
    Bambu cloud. printer_dispatched stays false. If the profile tree cannot be
    found, preset_files names each file as missing or inherits, and the result
    is a Bambu Studio handoff.
    """
    return slice_model(
        project_id=project_id,
        part_name=part_name,
        file_path=file_path,
        material=material,
        intent=intent,
        bed_type=bed_type,
    )


def fab_bambu_push_3mf(
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
        Field(
            description=(
                f"{material_list()}. Empty uses the part material, then {DEFAULT_MATERIAL}. "
                "Notes are not a slicer profile."
            )
        ),
    ] = "",
    intent: Annotated[str, Field(description="What this original training-tool or fab part is. Weapon-part requests are refused.")] = "",
    confirm: Annotated[
        bool,
        Field(description="True is required for a live push. Also requires dry_run false and BAMBU_PRINT_ENABLED=1."),
    ] = False,
    dry_run: Annotated[
        bool,
        Field(
            description=(
                "True (default) uploads nothing and queues nothing. "
                "False is a live push and still requires confirm true and BAMBU_PRINT_ENABLED=1."
            )
        ),
    ] = True,
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
        Field(
            description=(
                f"Empty uses {DEFAULT_BED_TYPE}. Also hot_plate, cool_plate, engineering_plate, or auto."
            )
        ),
    ] = "",
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
    """Slice geometry when needed, then upload a .gcode.3mf or leave a Studio handoff.

    Standing defaults are PLA, a textured plate, and a Bambu X1 Carbon with a
    0.4 mm nozzle, unless the part or this call sets a material or bed.
    An unsliced STL or 3MF is sliced with OrcaSlicer or Bambu Studio's CLI when
    that program is available. Full presets are loaded from FAB_SLICER_PRESETS or
    flattened from the installed slicer profile tree. The CLI does not expand
    inherits. Stock profiles often keep Cool Plate; the sliced file's plate
    metadata is set to Textured PEI Plate unless another bed is named.
    This server does not ship a Bambu or Orca vendor profile and does not call
    the Bambu cloud. Studio handoff is the fallback when the CLI or the profile
    tree is missing. A sliced file that is newer than the mesh is uploaded as-is.
    printer_dispatched is true only after the printer or Farm Manager accepts the job.
    An upload that is not accepted stays false. dry_run defaults to true and does not
    upload or queue. A live push needs dry_run false, confirm true, and BAMBU_PRINT_ENABLED=1.
    Raw .gcode is not a job. LAN Developer Mode (MQTT 8883 and implicit FTPS 990) is the
    print path. Farm Manager is used when transport is farm, or when BAMBU_FARM_URL is set
    and no LAN host is configured. Firearm and other weapon-part requests are refused.
    Training-tool and general fab jobs are in scope. Credentials come from the process
    environment, not from the repo.
    """
    return queue_print(
        project_id=project_id,
        part_name=part_name,
        file_path=file_path,
        material=material,
        intent=intent,
        confirm=confirm,
        dry_run=dry_run,
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


def _scrubbed(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        secrets = secret_values(load_config())
    except BambuError:
        secrets = ()
    scrubbed = scrub_obj(payload, secrets)
    if not isinstance(scrubbed, dict):
        return payload
    return scrubbed
