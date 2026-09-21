"""Queue a sliced Bambu job, or write a Studio handoff for an unsliced mesh."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Never

from techhand_print_fab.bambu_config import (
    BambuConfig,
    BambuError,
    UploadedNotAccepted,
    assert_private_host,
    farm_origin,
    load_config,
    scrub_obj,
    secret_values,
)
from techhand_print_fab.bambu_farm import FarmClient
from techhand_print_fab.bambu_frames import build_project_file, safe_remote_name, task_label
from techhand_print_fab.bambu_lan import send_lan
from techhand_print_fab.bambu_status import fetch_lan_status
from techhand_print_fab.exporting import ExportError, build_mesh, write_mesh_file
from techhand_print_fab.policy import screen, screen_weapon
from techhand_print_fab.print_files import (
    SliceInfo,
    classify_mesh,
    handoff_directory,
    notes_for,
    resolve_mesh,
    write_handoff,
)
from techhand_print_fab.profiles import (
    TARGET_NOZZLE_MM,
    TARGET_PRINTER,
    material_list,
    resolve_print_material,
)
from techhand_print_fab.results import print_result
from techhand_print_fab.slicer import SliceAttempt, attempt_slice, slice_output_for
from techhand_print_fab.spec import SpecError, normalize
from techhand_print_fab.store import StoreError, get_store, slugify

_BED_TYPES = frozenset({"auto", "hot_plate", "textured_plate", "cool_plate", "engineering_plate"})
_SERIAL = re.compile(r"^[A-Za-z0-9_-]{4,40}$")
_ACCESS = re.compile(r"^[A-Za-z0-9]{6,32}$")
_DEVICE = re.compile(r"^[A-Za-z0-9_-]{4,40}$")
TransportName = Literal["lan", "farm"]


@dataclass(frozen=True)
class _Opened:
    source: Path
    info: SliceInfo
    material: str
    bed_type: str
    notes: dict[str, Any] | None
    part_dir: Path | None
    part_name: str
    mesh_warning: str


def queue_print(
    *,
    project_id: str = "",
    part_name: str = "",
    file_path: str = "",
    material: str = "",
    intent: str = "",
    confirm: bool = False,
    dry_run: bool = True,
    transport: str = "",
    device_id: str = "",
    plate: int = 1,
    use_ams: bool = False,
    ams_slot: int | None = None,
    bed_leveling: bool = True,
    flow_cali: bool = True,
    vibration_cali: bool = True,
    timelapse: bool = False,
    bed_type: str = "",
    queue_only: bool = False,
) -> dict[str, Any]:
    try:
        config = load_config()
    except BambuError as exc:
        return print_result(ok=False, message=str(exc), printer_dispatched=False, mode="print_error")
    try:
        payload = _queue(
            config,
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
    except UploadedNotAccepted as exc:
        payload = print_result(
            ok=False,
            message=str(exc),
            printer_dispatched=False,
            mode="print_error",
            remote_url=exc.remote_url,
            f3mf_id=exc.f3mf_id,
        )
    except (BambuError, StoreError) as exc:
        payload = print_result(
            ok=False,
            message=str(exc),
            printer_dispatched=False,
            mode="print_error",
        )
    return _scrub(payload)


def slice_model(
    *,
    project_id: str = "",
    part_name: str = "",
    file_path: str = "",
    material: str = "",
    intent: str = "",
    bed_type: str = "",
) -> dict[str, Any]:
    """Write a .gcode.3mf with a local slicer CLI, or a Studio handoff. Never prints."""
    try:
        payload = _slice_model(
            project_id=project_id,
            part_name=part_name,
            file_path=file_path,
            material=material,
            intent=intent,
            bed_type=bed_type,
        )
    except (BambuError, StoreError) as exc:
        payload = print_result(ok=False, message=str(exc), printer_dispatched=False, mode="print_error")
    if payload.get("printer_dispatched"):
        raise BambuError("Slice cannot report a queued job.")
    return _scrub(payload)


def read_printer_status(*, transport: str = "", device_id: str = "") -> dict[str, Any]:
    """Nozzle, bed, and job progress. Never uploads and never sets printer_dispatched."""
    try:
        config = load_config()
    except BambuError as exc:
        return print_result(ok=False, message=str(exc), printer_dispatched=False, mode="print_error")
    secrets = secret_values(config)
    try:
        payload = _status(config, transport=transport, device_id=device_id)
    except (BambuError, StoreError) as exc:
        payload = print_result(ok=False, message=str(exc), printer_dispatched=False, mode="print_error")
    if payload.get("printer_dispatched"):
        raise BambuError("Status cannot report a queued job.")
    scrubbed = scrub_obj(payload, secrets)
    if not isinstance(scrubbed, dict):
        raise BambuError("Status result was empty.")
    return scrubbed


def send_farm(
    config: BambuConfig,
    filename: str,
    data: bytes,
    *,
    device_id: str,
    task_name: str,
    queue_only: bool,
    bed_leveling: bool,
    flow_cali: bool,
    timelapse: bool,
    ams_slot: int | None,
) -> dict[str, Any]:
    client = FarmClient(config)
    devices = client.list_devices()
    chosen = _choose_device(devices, device_id)
    mapping = [] if ams_slot is None else [{"ams_id": 0, "slot_id": ams_slot}]
    created = client.create_print(
        chosen,
        safe_remote_name(filename),
        data,
        task_name=task_name,
        queue_only=queue_only,
        bed_leveling=bed_leveling,
        flow_cali=flow_cali,
        timelapse=timelapse,
        ams_mapping=mapping,
    )
    matched = next((item for item in devices if item.get("serial") == chosen), None)
    warning = ""
    if matched is not None and (matched.get("model") or matched.get("name")) and not matched.get("x1c"):
        warning = (
            "This printer is not marked as an X1 Carbon. Profile notes are still X1 Carbon starting points."
        )
    return {**created, "warning": warning, "transport": "farm", "remote_url": ""}


def _queue(config: BambuConfig, **kwargs: Any) -> dict[str, Any]:
    project_id = str(kwargs["project_id"] or "")
    part_name = str(kwargs["part_name"] or "")
    file_path = str(kwargs["file_path"] or "")
    material = str(kwargs["material"] or "")
    intent = str(kwargs["intent"] or "")
    if len(intent) > 4000 or len(file_path) > 1024 or len(part_name) > 120:
        raise BambuError("A print field is too long.")
    plate = _bounded_int(kwargs["plate"], "plate", 1, 16)
    bed_leveling = _as_bool(kwargs["bed_leveling"], "bed_leveling")
    flow_cali = _as_bool(kwargs["flow_cali"], "flow_cali")
    vibration_cali = _as_bool(kwargs["vibration_cali"], "vibration_cali")
    timelapse = _as_bool(kwargs["timelapse"], "timelapse")
    use_ams = _as_bool(kwargs["use_ams"], "use_ams")
    queue_only = _as_bool(kwargs["queue_only"], "queue_only")
    confirm = kwargs["confirm"] is True
    dry_run = _as_bool(kwargs["dry_run"], "dry_run")
    bed_type = _bed(str(kwargs["bed_type"] or ""))
    ams_slot = _slot(kwargs["ams_slot"])
    device_id = str(kwargs["device_id"] or "").strip()
    if device_id and _DEVICE.fullmatch(device_id) is None:
        raise BambuError("device_id must be 4 to 40 letters, digits, underscores, or hyphens.")

    opened = _open_mesh(
        project_id=project_id,
        part_name=part_name,
        file_path=file_path,
        material=material,
        intent=intent,
        bed_type=bed_type,
    )
    if isinstance(opened, dict):
        return opened
    source = opened.source
    info = opened.info
    part_dir = opened.part_dir
    chosen = opened.material
    bed_type = opened.bed_type
    notes = opened.notes
    slice_prefix = ""
    profile_applied = False
    slicer_name = ""
    preset_files: tuple[dict[str, str], ...] | None = None
    sliced_plate = ""
    if not info.sliced:
        attempt = attempt_slice(
            source,
            slice_output_for(part_dir, source),
            material=chosen,
            bed_type=bed_type,
        )
        preset_files = attempt.preset_files
        sliced_plate = attempt.sliced_plate
        if attempt.output is None:
            return _fallback_result(opened, attempt, plate=plate)
        source = attempt.output
        info = classify_mesh(source)
        profile_applied = attempt.profile_applied
        slicer_name = attempt.slicer
        slice_prefix = attempt.message + " "
    label = task_label(part_name or source.stem)
    common = _common(
        notes=notes,
        profile_applied=profile_applied,
        material=chosen,
        bed_type=bed_type,
        source=source,
        plate=plate,
        slicer=slicer_name,
        mesh_warning=opened.mesh_warning,
        preset_files=preset_files,
        sliced_plate=sliced_plate,
    )

    if plate not in info.plates:
        raise BambuError(f"Plate {plate} is not in this file. Available plates: {list(info.plates)}.")
    try:
        transport_name = resolve_transport(config, str(kwargs["transport"] or ""))
    except BambuError as exc:
        unconfigured = str(exc).startswith("Set BAMBU_LAN_HOST")
        if not unconfigured or (confirm and config.print_enabled and not dry_run):
            raise
        return print_result(
            ok=True,
            message=_prefixed(slice_prefix, str(exc)),
            printer_dispatched=False,
            mode="dry_run" if dry_run else "print_plan",
            dry_run=dry_run,
            sliced=True,
            transport="",
            request=None,
            missing=["BAMBU_LAN_HOST", "BAMBU_ACCESS_CODE", "BAMBU_SERIAL"],
            kind=info.kind,
            **common,
        )
    _check_secret_shape(config, transport_name)
    remote_name = safe_remote_name(source.name)
    ams_mapping = [0 if ams_slot is None else ams_slot] if use_ams else []
    preview = build_project_file(
        sequence_id="0",
        plate=plate,
        url=f"ftp:///model/{remote_name}",
        task_name=label,
        bed_type=bed_type,
        bed_leveling=bed_leveling,
        flow_cali=flow_cali,
        vibration_cali=vibration_cali,
        timelapse=timelapse,
        use_ams=use_ams,
        ams_mapping=ams_mapping,
    )
    lan_queue_only = queue_only and transport_name == "lan"
    wants_send = (not dry_run) and confirm and config.print_enabled and not lan_queue_only
    missing = _missing_for(config, transport_name)
    if wants_send and missing:
        raise BambuError("Print needs " + ", ".join(missing) + ".")
    if not wants_send:
        gates = []
        if dry_run:
            gates.append("dry_run")
        if not confirm:
            gates.append("confirm")
        if not config.print_enabled:
            gates.append("BAMBU_PRINT_ENABLED")
        if dry_run:
            message = (
                "Dry run. No file was uploaded and no job was queued. "
                "printer_dispatched stays false. A live push needs dry_run false, confirm true, "
                "and BAMBU_PRINT_ENABLED=1."
            )
            mode = "dry_run"
        elif lan_queue_only:
            message = (
                "queue_only on LAN does not upload. The printer starts the job when it accepts "
                "project_file. Pass queue_only false, with dry_run false, confirm true, "
                "and BAMBU_PRINT_ENABLED=1, to print."
            )
            mode = "print_plan"
        else:
            message = (
                "Sliced file is ready. No printer job was sent. "
                "Set BAMBU_PRINT_ENABLED=1 and pass dry_run false with confirm true to send it."
            )
            mode = "print_plan"
        return print_result(
            ok=True,
            message=_prefixed(slice_prefix, message),
            printer_dispatched=False,
            mode=mode,
            dry_run=dry_run,
            sliced=True,
            transport=transport_name,
            request=preview if transport_name == "lan" else {"transport": "farm", "queue_only": queue_only},
            missing=[*missing, *gates],
            kind=info.kind,
            **common,
        )

    data = source.read_bytes()
    sent = _dispatch(
        transport_name,
        config,
        remote_name,
        data,
        plate=plate,
        task_name=label,
        bed_type=bed_type,
        bed_leveling=bed_leveling,
        flow_cali=flow_cali,
        vibration_cali=vibration_cali,
        timelapse=timelapse,
        use_ams=use_ams,
        ams_mapping=ams_mapping,
        device_id=device_id,
        queue_only=queue_only,
        ams_slot=ams_slot,
    )
    if transport_name == "farm" and queue_only:
        message = "Farm Manager accepted the task with task_print_model 0 (queue)."
    elif transport_name == "farm":
        message = "Farm Manager accepted the print task."
    else:
        message = "The printer accepted the sliced job."
    return print_result(
        ok=True,
        message=_prefixed(slice_prefix, message),
        printer_dispatched=True,
        mode="print_dispatch",
        sliced=True,
        kind=info.kind,
        warning=str(sent.get("warning") or ""),
        remote_url=str(sent.get("remote_url") or ""),
        ack_result=str(sent.get("ack_result") or ""),
        task_id=str(sent.get("task_id") or ""),
        f3mf_id=str(sent.get("f3mf_id") or ""),
        device_id=str(sent.get("device_id") or ""),
        transport=transport_name,
        request=sent.get("request"),
        dry_run=False,
        queued=True,
        **common,
    )


def resolve_transport(config: BambuConfig, requested: str) -> TransportName:
    choice = (requested or config.transport or "").strip().lower()
    if choice not in {"", "lan", "farm"}:
        raise BambuError("transport must be lan or farm.")
    if choice == "":
        if config.lan_host or config.access_code or config.serial:
            choice = "lan"
        elif config.farm_url:
            choice = "farm"
        else:
            raise BambuError("Set BAMBU_LAN_HOST, BAMBU_ACCESS_CODE, and BAMBU_SERIAL, or set BAMBU_FARM_URL.")
    match choice:
        case "lan":
            if config.lan_host:
                assert_private_host(config.lan_host, allow_nonprivate=config.allow_nonprivate)
            return "lan"
        case "farm":
            if config.farm_url:
                farm_origin(config.farm_url, allow_nonprivate=config.allow_nonprivate)
            return "farm"
        case _ as unknown:
            _never(unknown)


def _dispatch(transport_name: TransportName, config: BambuConfig, filename: str, data: bytes, **kwargs: Any) -> dict[str, Any]:
    match transport_name:
        case "lan":
            return send_lan(
                config,
                filename,
                data,
                plate=kwargs["plate"],
                task_name=kwargs["task_name"],
                bed_type=kwargs["bed_type"],
                bed_leveling=kwargs["bed_leveling"],
                flow_cali=kwargs["flow_cali"],
                vibration_cali=kwargs["vibration_cali"],
                timelapse=kwargs["timelapse"],
                use_ams=kwargs["use_ams"],
                ams_mapping=kwargs["ams_mapping"],
            )
        case "farm":
            return send_farm(
                config,
                filename,
                data,
                device_id=kwargs["device_id"],
                task_name=kwargs["task_name"],
                queue_only=kwargs["queue_only"],
                bed_leveling=kwargs["bed_leveling"],
                flow_cali=kwargs["flow_cali"],
                timelapse=kwargs["timelapse"],
                ams_slot=kwargs["ams_slot"] if kwargs["use_ams"] else None,
            )
        case _ as unknown:
            _never(unknown)


def _load_part(
    store: Any,
    project_id: str,
    part_name: str,
    file_path: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Path | None, Path | None, str]:
    if not project_id and not part_name:
        if not file_path.strip():
            raise BambuError("Pass project_id and part_name, or file_path.")
        return None, None, None, None, "original"
    if not project_id or not part_name:
        raise BambuError("project_id and part_name are a pair.")
    project = store.read_project(project_id)
    if project is None:
        raise BambuError("unknown project id")
    slug = slugify(part_name)
    record = store.read_part(project_id, slug)
    project_dir = store.project_dir(project_id)
    if record is None:
        if not file_path.strip():
            raise BambuError("unknown part")
        return project, None, None, project_dir, "original"
    return project, record, store.part_dir(project_id, slug), project_dir, str(record.get("reproduction") or "original")


def _blocked(texts: list[str], reproduction: str) -> dict[str, Any] | None:
    blocked = screen(*texts, reproduction=reproduction)
    if blocked:
        return blocked
    return screen_weapon(*texts)


def _policy_texts(
    intent: str,
    file_path: str,
    part_name: str,
    project: dict[str, Any] | None,
    record: dict[str, Any] | None,
) -> list[str]:
    texts = [intent, part_name, Path(file_path).name if file_path else ""]
    if project is not None:
        texts.append(str(project.get("name") or ""))
        texts.append(str(project.get("description") or ""))
    if record is not None:
        texts.append(str(record.get("name") or ""))
        texts.append(str(record.get("intent") or ""))
        spec = record.get("spec") if isinstance(record.get("spec"), dict) else {}
        texts.append(str(spec.get("notes") or ""))
        texts.append(str(spec.get("material") or ""))
    return texts


def _slice_model(
    *,
    project_id: str,
    part_name: str,
    file_path: str,
    material: str,
    intent: str,
    bed_type: str,
) -> dict[str, Any]:
    if len(intent) > 4000 or len(file_path) > 1024 or len(part_name) > 120:
        raise BambuError("A print field is too long.")
    opened = _open_mesh(
        project_id=project_id,
        part_name=part_name,
        file_path=file_path,
        material=material,
        intent=intent,
        bed_type=_bed(bed_type),
    )
    if isinstance(opened, dict):
        return opened
    if opened.info.sliced:
        return print_result(
            ok=True,
            message="This file is already a sliced .gcode.3mf. No slicer was run and no printer job was sent.",
            printer_dispatched=False,
            mode="sliced",
            sliced=True,
            kind=opened.info.kind,
            **_common(
                notes=opened.notes,
                profile_applied=False,
                material=opened.material,
                bed_type=opened.bed_type,
                source=opened.source,
                plate=1,
                slicer="",
                mesh_warning=opened.mesh_warning,
            ),
        )
    attempt = attempt_slice(
        opened.source,
        slice_output_for(opened.part_dir, opened.source),
        material=opened.material,
        bed_type=opened.bed_type,
    )
    if attempt.output is None:
        return _fallback_result(opened, attempt, plate=1)
    info = classify_mesh(attempt.output)
    return print_result(
        ok=True,
        message=attempt.message + " No printer job was sent.",
        printer_dispatched=False,
        mode="sliced",
        sliced=True,
        kind=info.kind,
        **_common(
            notes=opened.notes,
            profile_applied=True,
            material=opened.material,
            bed_type=opened.bed_type,
            source=attempt.output,
            plate=1,
            slicer=attempt.slicer,
            mesh_warning=opened.mesh_warning,
            preset_files=attempt.preset_files,
            sliced_plate=attempt.sliced_plate,
        ),
    )


def _open_mesh(
    *,
    project_id: str,
    part_name: str,
    file_path: str,
    material: str,
    intent: str,
    bed_type: str,
) -> _Opened | dict[str, Any]:
    store = get_store()
    project, record, part_dir, project_dir, reproduction = _load_part(store, project_id, part_name, file_path)
    texts = _policy_texts(intent, file_path, part_name, project, record)
    blocked = _blocked(texts, reproduction)
    if blocked:
        return blocked
    chosen = _material(material, record)
    warning = _ensure_geometry(part_dir, record, file_path)
    source = resolve_mesh(file_path, part_dir=part_dir, project_dir=project_dir)
    texts.append(source.name)
    blocked = _blocked(texts, reproduction)
    if blocked:
        return blocked
    return _Opened(
        source=source,
        info=classify_mesh(source),
        material=chosen,
        bed_type=bed_type,
        notes=notes_for(chosen),
        part_dir=part_dir,
        part_name=part_name,
        mesh_warning=warning,
    )


def _ensure_geometry(part_dir: Path | None, record: dict[str, Any] | None, file_path: str) -> str:
    """Write model.stl from the parametric spec when the part has no mesh yet."""
    if file_path.strip() or part_dir is None or record is None:
        return ""
    names = ("model.gcode.3mf", "model.3mf", "model.stl")
    if any((part_dir / name).is_file() and not (part_dir / name).is_symlink() for name in names):
        return ""
    spec_raw = record.get("spec")
    if not isinstance(spec_raw, dict):
        return ""
    try:
        spec = normalize(spec_raw)
        built = build_mesh(spec, part_dir / "model.scad")
        write_mesh_file(built.mesh, part_dir / "model.stl", "stl", str(record.get("slug") or "part"))
    except (SpecError, ExportError, OSError, ValueError) as exc:
        raise BambuError(str(exc)) from exc
    return built.warning


def _fallback_result(opened: _Opened, attempt: SliceAttempt, *, plate: int) -> dict[str, Any]:
    directory = write_handoff(
        opened.source,
        directory=handoff_directory(opened.part_dir, opened.source),
        material_notes=opened.notes,
        material=opened.material,
        bed_type=opened.bed_type,
        detail=attempt.message,
    )
    return print_result(
        ok=attempt.ok,
        message=attempt.message,
        printer_dispatched=False,
        mode=attempt.mode,
        sliced=False,
        handoff_dir=str(directory),
        kind=opened.info.kind,
        missing=list(attempt.missing),
        **_common(
            notes=opened.notes,
            profile_applied=False,
            material=opened.material,
            bed_type=opened.bed_type,
            source=opened.source,
            plate=plate,
            slicer=attempt.slicer,
            mesh_warning=opened.mesh_warning,
            preset_files=attempt.preset_files,
            sliced_plate=attempt.sliced_plate,
        ),
    )


def _common(
    *,
    notes: dict[str, Any] | None,
    profile_applied: bool,
    material: str,
    bed_type: str,
    source: Path,
    plate: int,
    slicer: str,
    mesh_warning: str,
    preset_files: tuple[dict[str, str], ...] | None = None,
    sliced_plate: str = "",
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "profile_notes": notes,
        "profile_applied": profile_applied,
        "material": material,
        "bed_type": bed_type,
        "file": str(source),
        "plate": plate,
        "slicer": slicer,
        "printer": TARGET_PRINTER,
        "nozzle_mm": TARGET_NOZZLE_MM,
    }
    if mesh_warning:
        fields["mesh_warning"] = mesh_warning
    if preset_files:
        fields["preset_files"] = [dict(item) for item in preset_files]
    if sliced_plate:
        fields["sliced_plate"] = sliced_plate
    return fields


def _prefixed(prefix: str, message: str) -> str:
    if not prefix:
        return message
    return prefix + message


def _scrub(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        secrets = secret_values(load_config())
    except BambuError:
        secrets = ()
    scrubbed = scrub_obj(payload, secrets)
    if not isinstance(scrubbed, dict):
        raise BambuError("Print result was empty.")
    return scrubbed


def _material(material: str, record: dict[str, Any] | None) -> str:
    spec = record.get("spec") if record is not None and isinstance(record.get("spec"), dict) else {}
    chosen = resolve_print_material(material, str(spec.get("material") or ""))
    if not chosen:
        raise BambuError(f"material must be {material_list()}.")
    return chosen


def _bed(bed_type: str) -> str:
    text = bed_type.strip().lower()
    if not text:
        return "textured_plate"
    if text not in _BED_TYPES:
        raise BambuError("bed_type must be auto, hot_plate, textured_plate, cool_plate, or engineering_plate.")
    return text


def _missing_for(config: BambuConfig, transport_name: TransportName) -> list[str]:
    match transport_name:
        case "lan":
            missing = []
            if not config.lan_host:
                missing.append("BAMBU_LAN_HOST")
            if not config.access_code:
                missing.append("BAMBU_ACCESS_CODE")
            if not config.serial:
                missing.append("BAMBU_SERIAL")
            return missing
        case "farm":
            missing = []
            if not config.farm_url:
                missing.append("BAMBU_FARM_URL")
            if not config.farm_token and not (config.farm_username and config.farm_password):
                missing.append("BAMBU_FARM_TOKEN")
            return missing
        case _ as unknown:
            _never(unknown)


def _check_secret_shape(config: BambuConfig, transport_name: TransportName) -> None:
    if transport_name == "lan" and config.access_code and _ACCESS.fullmatch(config.access_code) is None:
        raise BambuError("BAMBU_ACCESS_CODE must be 6 to 32 letters or digits.")
    if transport_name == "lan" and config.serial and _SERIAL.fullmatch(config.serial) is None:
        raise BambuError("BAMBU_SERIAL must be 4 to 40 letters, digits, underscores, or hyphens.")


def _choose_device(devices: list[dict[str, Any]], device_id: str) -> str:
    ids = [str(item.get("serial")) for item in devices if item.get("serial")]
    if device_id:
        if ids and device_id not in ids:
            raise BambuError("device_id is not on this Farm Manager server.")
        return device_id
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise BambuError("Farm Manager returned no printers. Pass device_id or bind the X1 Carbon.")
    raise BambuError("Farm Manager has more than one printer. Pass device_id from fab_bambu_discover.")


def _bounded_int(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BambuError(f"{name} must be an integer.")
    if value < low or value > high:
        raise BambuError(f"{name} must be from {low} to {high}.")
    return value


def _slot(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise BambuError("ams_slot must be an integer.")
    if value < 0 or value > 15:
        raise BambuError("ams_slot must be from 0 to 15.")
    return value


def _as_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise BambuError(f"{name} must be a boolean.")
    return value


def _status(config: BambuConfig, *, transport: str, device_id: str) -> dict[str, Any]:
    if device_id and _DEVICE.fullmatch(device_id) is None:
        raise BambuError("device_id must be 4 to 40 letters, digits, underscores, or hyphens.")
    transport_name = resolve_transport(config, transport)
    missing = _missing_for(config, transport_name)
    if missing:
        raise BambuError("Status needs " + ", ".join(missing) + ".")
    _check_secret_shape(config, transport_name)
    match transport_name:
        case "lan":
            snapshot = fetch_lan_status(config, config.lan_host, config.serial)
            return print_result(
                ok=True,
                message="Printer status. No file was uploaded and no job was queued.",
                printer_dispatched=False,
                mode="status",
                transport="lan",
                host=config.lan_host,
                serial=config.serial,
                model="",
                **snapshot,
            )
        case "farm":
            devices = FarmClient(config).list_devices()
            chosen = _choose_device(devices, device_id)
            device = next((item for item in devices if str(item.get("serial")) == chosen), None)
            if device is None:
                raise BambuError("device_id is not on this Farm Manager server.")
            return print_result(
                ok=True,
                message=(
                    "Farm Manager device status. Nozzle and bed stay null when the device report omits them. "
                    "No file was uploaded and no job was queued."
                ),
                printer_dispatched=False,
                mode="status",
                transport="farm",
                host=str(device.get("host") or ""),
                serial=chosen,
                model=str(device.get("model") or ""),
                state=str(device.get("state") or ""),
                nozzle_c=device.get("nozzle_c"),
                nozzle_target_c=device.get("nozzle_target_c"),
                bed_c=device.get("bed_c"),
                bed_target_c=device.get("bed_target_c"),
                progress_percent=device.get("progress_percent"),
                remaining_minutes=device.get("remaining_minutes"),
                layer=device.get("layer"),
                total_layers=device.get("total_layers"),
                job_name=str(device.get("job_name") or ""),
                ams=device.get("ams"),
                device_id=chosen,
            )
        case _ as unknown:
            _never(unknown)


def _never(value: Never) -> Never:
    raise RuntimeError(f"Unhandled transport: {value}")
