"""Headless slice through a local OrcaSlicer or Bambu Studio CLI.

The CLI is the same family Bambu Studio documents: ``--slice``, ``--export-3mf``,
``--load-settings``, and ``--load-filaments``. Full JSON presets are required
because that CLI does not expand ``inherits``. This module does not call the
Bambu cloud and does not ship a vendor profile.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Never

from techhand_print_fab.bambu_config import BambuError
from techhand_print_fab.preset_expand import (
    ExpandError,
    PresetFileReport,
    align_process_bed,
    ensure_presets,
    find_profile_root,
    format_incomplete,
    inspect_presets,
    missing_reports,
    preset_destination,
    preset_paths,
    presets_ready,
)
from techhand_print_fab.print_files import MAX_MESH_BYTES, plate_numbers
from techhand_print_fab.profiles import (
    DEFAULT_BED_TYPE,
    DEFAULT_MATERIAL,
    ORCA_TEXTURED_PEI,
    TARGET_NOZZLE_MM,
    TARGET_PRINTER,
    TEXTURED_PEI_PLATE_ID,
)

SLICE_TIMEOUT_S = 360
_ORCA_NAMES = ("orca-slicer", "OrcaSlicer")
_BAMBU_NAMES = ("bambu-studio", "BambuStudio")
_HIDDEN_ENV = frozenset(
    {
        "BAMBU_ACCESS_CODE",
        "BAMBU_FARM_TOKEN",
        "BAMBU_FARM_PASSWORD",
        "BAMBU_FARM_KEY_FILE",
    }
)
_ORCA_BEDS = {
    "hot_plate": "High Temp Plate",
    "textured_plate": ORCA_TEXTURED_PEI,
    "cool_plate": "Cool Plate",
    "engineering_plate": "Engineering Plate",
}
SlicerFamily = Literal["orca", "bambu_studio"]


@dataclass(frozen=True)
class SlicerBin:
    path: str
    family: SlicerFamily
    problem: str = ""


@dataclass(frozen=True)
class SliceAttempt:
    ok: bool
    mode: str
    message: str
    output: Path | None
    slicer: str
    profile_applied: bool
    missing: tuple[str, ...]
    preset_files: tuple[dict[str, str], ...] = ()
    sliced_plate: str = ""
    plate_id: str = ""


@dataclass(frozen=True)
class _Presets:
    machine: Path
    process: Path
    filament: Path


def fallback_help() -> str:
    """What to install when the CLI or the full presets are not ready."""
    return (
        "Headless slice needs OrcaSlicer or Bambu Studio's CLI "
        "(orca-slicer or bambu-studio on PATH, or ORCA_SLICER_BIN / BAMBU_STUDIO_BIN). "
        "Full presets live in FAB_SLICER_PRESETS or the local cache: machine.json, process.json, "
        "and filament/<MATERIAL>.json. The CLI does not expand inherits. "
        "techhand-print-fab --expand-presets flattens them from the installed slicer's "
        "resources/profiles (FAB_SLICER_PROFILE_ROOT). "
        "This server does not ship Bambu or Orca vendor profiles and does not call the Bambu cloud. "
        f"Standing defaults are {DEFAULT_MATERIAL}, {DEFAULT_BED_TYPE}, "
        f"{TARGET_PRINTER}, {TARGET_NOZZLE_MM:.1f} mm nozzle. "
        f"process.json curr_bed_type is set to {_ORCA_BEDS[DEFAULT_BED_TYPE]} unless another bed is named. "
        "Bambu Studio can slice the handoff mesh until that CLI is set up."
    )


def find_slicer() -> SlicerBin | None:
    """The explicit env bin wins. A bad explicit path is not replaced by PATH."""
    orca_env = os.environ.get("ORCA_SLICER_BIN", "").strip()
    if orca_env:
        return _from_env(orca_env, "orca", "ORCA_SLICER_BIN")
    bambu_env = os.environ.get("BAMBU_STUDIO_BIN", "").strip()
    if bambu_env:
        return _from_env(bambu_env, "bambu_studio", "BAMBU_STUDIO_BIN")
    for name in _ORCA_NAMES:
        found = shutil.which(name)
        if found:
            return SlicerBin(found, "orca")
    for name in _BAMBU_NAMES:
        found = shutil.which(name)
        if found:
            return SlicerBin(found, "bambu_studio")
    return None


def orca_bed_name(bed_type: str) -> str | None:
    """Orca ``curr_bed_type`` enum value. ``auto`` leaves the preset's plate."""
    if bed_type == "auto":
        return None
    try:
        return _ORCA_BEDS[bed_type]
    except KeyError as exc:
        raise BambuError(
            "bed_type must be auto, hot_plate, textured_plate, cool_plate, or engineering_plate."
        ) from exc


def slice_output_for(part_dir: Path | None, source: Path) -> Path:
    name = source.name
    lower = name.lower()
    if lower.endswith(".stl"):
        stem = name[: -len(".stl")]
    elif lower.endswith(".3mf"):
        stem = name[: -len(".3mf")]
    else:
        raise BambuError("Only an .stl or geometry .3mf can be sliced.")
    stem = stem.strip().strip(".")
    if not stem:
        stem = "model"
    filename = f"{stem}.gcode.3mf"
    parent = part_dir if part_dir is not None else source.parent
    if part_dir is not None and source.parent.resolve() == part_dir.resolve() and lower in {
        "model.stl",
        "model.3mf",
    }:
        return part_dir / "model.gcode.3mf"
    return parent / filename


def build_slice_argv(
    binary: str,
    source: Path,
    output: Path,
    *,
    machine: Path,
    process: Path,
    filament: Path,
    bed_type: str,
) -> list[str]:
    """Orca CLI. ``--load-settings`` is process then machine, matching Orca's docs."""
    _reject_separator(process)
    _reject_separator(machine)
    _reject_separator(filament)
    argv = [
        binary,
        str(source.resolve()),
        "--load-settings",
        f"{process.resolve()};{machine.resolve()}",
        "--load-filaments",
        str(filament.resolve()),
        "--load-defaultfila",
        "--arrange",
        "1",
        "--slice",
        "0",
        "--mstpp",
        "300",
        "--export-3mf",
        str(output),
        "--nozzle-diameter",
        f"{TARGET_NOZZLE_MM:.1f}",
    ]
    bed = orca_bed_name(bed_type)
    if bed is not None:
        argv.extend(["--curr-bed-type", bed])
    return argv


def child_env() -> dict[str, str]:
    """Slicer subprocess environment with printer secrets removed."""
    return {key: value for key, value in os.environ.items() if key not in _HIDDEN_ENV}


def attempt_slice(
    source: Path,
    output: Path,
    *,
    material: str,
    bed_type: str,
) -> SliceAttempt:
    binary = find_slicer()
    dest, dest_problem = preset_destination()
    if binary is None or binary.problem:
        problem = binary.problem if binary is not None else "No OrcaSlicer or Bambu Studio CLI is on PATH."
        reports = inspect_presets(dest, material) if dest is not None else missing_reports(material)
        message = (
            f"{problem}\n"
            f"{format_incomplete(reports, dest, profile_root=None, profile_note='')}\n"
            f"{fallback_help()}"
        )
        missing = ("orca-slicer or bambu-studio",) + _not_ready(reports)
        return _handoff(missing, message, "", reports)
    if dest is None:
        reports = missing_reports(material)
        message = (
            f"{dest_problem}\n"
            f"{format_incomplete(reports, None, profile_root=None, profile_note='')}\n"
            f"{fallback_help()}"
        )
        return _handoff(_not_ready(reports), message, slicer_label(binary.family), reports)
    reports = inspect_presets(dest, material)
    profile_root: Path | None = None
    profile_note = ""
    if not presets_ready(reports):
        profile_root, profile_note = find_profile_root(Path(binary.path))
        if profile_root is not None:
            try:
                reports = ensure_presets(
                    dest,
                    profile_root,
                    material,
                    orca_bed_name(bed_type),
                    force=False,
                )
            except ExpandError as exc:
                profile_note = str(exc)
            else:
                profile_note = ""
        if not presets_ready(reports):
            message = (
                f"{format_incomplete(reports, dest, profile_root=profile_root, profile_note=profile_note)}\n"
                f"{fallback_help()}"
            )
            return _handoff(_not_ready(reports), message, slicer_label(binary.family), reports)
    _machine, process, _filament = preset_paths(dest, material)
    try:
        align_process_bed(process, orca_bed_name(bed_type))
    except ExpandError as exc:
        reports = inspect_presets(dest, material)
        message = (
            f"{exc}\n"
            f"{format_incomplete(reports, dest, profile_root=profile_root, profile_note='')}\n"
            f"{fallback_help()}"
        )
        return _handoff(_not_ready(reports) or ("process.json",), message, slicer_label(binary.family), reports)
    presets = _Presets(*preset_paths(dest, material))
    reports = inspect_presets(dest, material)
    return _invoke(
        binary,
        source,
        output,
        presets,
        material=material,
        bed_type=bed_type,
        reports=reports,
    )


def slicer_label(family: SlicerFamily) -> str:
    match family:
        case "orca":
            return "OrcaSlicer"
        case "bambu_studio":
            return "Bambu Studio"
        case _ as unknown:
            _never(unknown)


def _invoke(
    binary: SlicerBin,
    source: Path,
    output: Path,
    presets: _Presets,
    *,
    material: str,
    bed_type: str,
    reports: tuple[PresetFileReport, ...],
) -> SliceAttempt:
    label = slicer_label(binary.family)
    if output.is_symlink():
        raise BambuError("Refusing to write sliced output through a symlink.")
    if output.exists() and not output.is_file():
        raise BambuError("Sliced output path is not a file.")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.name}.partial")
    if partial.is_symlink():
        raise BambuError("Refusing to write sliced output through a symlink.")
    if partial.exists():
        partial.unlink()
    argv = build_slice_argv(
        binary.path,
        source,
        partial,
        machine=presets.machine,
        process=presets.process,
        filament=presets.filament,
        bed_type=bed_type,
    )
    try:
        completed = subprocess.run(
            argv,
            cwd=str(output.parent),
            capture_output=True,
            timeout=SLICE_TIMEOUT_S,
            check=False,
            env=child_env(),
        )
    except subprocess.TimeoutExpired:
        _unlink(partial)
        return _failed(label, f"{label} timed out after {SLICE_TIMEOUT_S} seconds.", reports)
    except OSError as exc:
        _unlink(partial)
        return _failed(label, f"{label} could not be started ({exc.__class__.__name__}).", reports)
    if completed.returncode != 0 or not partial.is_file() or partial.is_symlink():
        _unlink(partial)
        detail = _tail(completed.stderr or completed.stdout)
        suffix = f" {detail}" if detail else ""
        return _failed(label, f"{label} exited {completed.returncode} without a sliced file.{suffix}", reports)
    if partial.stat().st_size <= 0 or partial.stat().st_size > MAX_MESH_BYTES:
        _unlink(partial)
        return _failed(label, f"{label} wrote an empty file or a file over 200 MB.", reports)
    try:
        plates = plate_numbers(partial)
    except BambuError as exc:
        _unlink(partial)
        return _failed(label, f"{label} did not write a sliced 3MF ({exc}).", reports)
    if not plates:
        _unlink(partial)
        return _failed(label, f"{label} did not write Metadata/plate_N.gcode.", reports)
    plate_name = orca_bed_name(bed_type) or ""
    plate_id = TEXTURED_PEI_PLATE_ID if bed_type == DEFAULT_BED_TYPE else ""
    if plate_name:
        try:
            apply_bed_metadata(partial, plate_name, plate_id=plate_id)
            if plate_id:
                _reject_cool_plate_tag(partial)
        except BambuError as exc:
            _unlink(partial)
            return _failed(label, str(exc), reports)
        try:
            plates = plate_numbers(partial)
        except BambuError as exc:
            _unlink(partial)
            return _failed(label, f"{label} plate metadata could not be read ({exc}).", reports)
        if not plates:
            _unlink(partial)
            return _failed(label, f"{label} lost Metadata/plate_N.gcode while setting the plate.", reports)
    if output.is_symlink() or (output.exists() and not output.is_file()):
        _unlink(partial)
        raise BambuError("Refusing to replace a sliced output that is not a regular file.")
    try:
        os.replace(partial, output)
    except OSError as exc:
        _unlink(partial)
        raise BambuError("Could not move the sliced file into place.") from exc
    plate_note = ""
    if plate_name:
        plate_note = f" Plate metadata is {plate_name}."
        if plate_id:
            plate_note = f" Plate metadata is {plate_name} ({plate_id})."
    return SliceAttempt(
        ok=True,
        mode="sliced",
        message=(
            f"{label} wrote {output.name} for {TARGET_PRINTER}, "
            f"{TARGET_NOZZLE_MM:.1f} mm nozzle, {material}, {bed_type}.{plate_note}"
        ),
        output=output,
        slicer=label,
        profile_applied=True,
        missing=(),
        preset_files=_report_rows(reports),
        sliced_plate=plate_name,
        plate_id=plate_id,
    )


def _handoff(
    missing: tuple[str, ...],
    message: str,
    slicer: str,
    reports: tuple[PresetFileReport, ...],
) -> SliceAttempt:
    return SliceAttempt(
        ok=True,
        mode="studio_handoff",
        message=message,
        output=None,
        slicer=slicer,
        profile_applied=False,
        missing=missing,
        preset_files=_report_rows(reports),
    )


def _failed(slicer: str, message: str, reports: tuple[PresetFileReport, ...] = ()) -> SliceAttempt:
    return SliceAttempt(
        ok=False,
        mode="slice_error",
        message=f"{message} {fallback_help()}",
        output=None,
        slicer=slicer,
        profile_applied=False,
        missing=(),
        preset_files=_report_rows(reports),
    )


def _from_env(value: str, family: SlicerFamily, variable: str) -> SlicerBin:
    path = _executable_file(value)
    if path is None:
        shown = value if len(value) <= 180 else value[:180]
        return SlicerBin("", family, f"{variable} is not an executable file ({shown}).")
    return SlicerBin(str(path), family)


def _executable_file(value: str) -> Path | None:
    if not value or len(value) > 4096:
        return None
    path = Path(value).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return path


def _not_ready(reports: tuple[PresetFileReport, ...]) -> tuple[str, ...]:
    return tuple(item.file for item in reports if item.status != "ok")


def _report_rows(reports: tuple[PresetFileReport, ...]) -> tuple[dict[str, str], ...]:
    return tuple(item.as_dict() for item in reports)


_PLATE_LABELS = frozenset(
    {
        "Cool Plate",
        "Engineering Plate",
        "High Temp Plate",
        "Hot Plate",
        "Textured PEI Plate",
        "Textured Cool Plate",
        "Supertack Plate",
    }
)
_PLATE_TOKENS = frozenset(
    {
        "hot_plate",
        "textured_plate",
        "cool_plate",
        "engineering_plate",
        "eng_plate",
        "textured_cool_plate",
        "supertack_plate",
    }
)
_COOL_TAGS = frozenset({"Cool Plate", "cool_plate", "btPC"})
_CURR_BED_JSON = re.compile(r'("curr_bed_type"\s*:\s*")([^"]*)(")')
_BED_JSON = re.compile(r'("bed_type"\s*:\s*")([^"]*)(")')
_CURR_BED_XML = re.compile(r'(key="curr_bed_type"\s+value=")([^"]*)(")')
_BED_XML = re.compile(r'(key="bed_type"\s+value=")([^"]*)(")')
_GCODE_BED = re.compile(r'^(\s*;\s*(?:curr_bed_type|bed_type|plate_id)\s*=\s*)(.*?)(\s*)$', re.MULTILINE)
_SELECTED_VALUE = re.compile(
    r'(?:"(?:curr_bed_type|bed_type|plate_id)"\s*:\s*"|key="(?:curr_bed_type|bed_type|plate_id)"\s+value="|;\s*(?:curr_bed_type|bed_type|plate_id)\s*=\s*)([^"\n]+)'
)


def apply_bed_metadata(path: Path, bed_label: str, *, plate_id: str = "") -> None:
    """Rewrite the selected plate inside a sliced 3MF.

    Orca can leave Cool Plate or ``cool_plate`` in ``slice_info.config``,
    ``project_settings.config``, and the gcode header after ``--curr-bed-type``.
    The X1 Carbon dogfood plate is Textured PEI. ``plate_id`` ``textured_pei``
    is written for that default. Display names stay on Orca's enum, so
    ``curr_bed_type`` is ``Textured PEI Plate`` when the file used a display name.
    """
    if path.is_symlink():
        raise BambuError("Refusing to rewrite sliced metadata through a symlink.")
    temporary = path.with_name(f".{path.name}.bed")
    if temporary.is_symlink():
        raise BambuError("Refusing to rewrite sliced metadata through a symlink.")
    if temporary.exists():
        temporary.unlink()
    try:
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(temporary, "w") as target:
            for info in source.infolist():
                data = source.read(info.filename)
                target.writestr(info, _patch_member(info.filename, data, bed_label, plate_id))
        os.replace(temporary, path)
    except (OSError, zipfile.BadZipFile) as exc:
        _unlink(temporary)
        raise BambuError("Could not set the plate in the sliced file.") from exc


def _patch_member(name: str, data: bytes, bed_label: str, plate_id: str) -> bytes:
    lower = name.lower()
    if not lower.startswith("metadata/"):
        return data
    if lower.endswith(".gcode"):
        return _patch_gcode(data, bed_label, plate_id)
    if lower.endswith((".json", ".config", ".xml")):
        return _patch_config(data, bed_label, plate_id)
    return data


def _locked_plate_value(current: str, bed_label: str, plate_id: str) -> str:
    """Display names stay on Orca's enum. Snake tags use the plate id."""
    text = current.strip()
    if plate_id and text in _PLATE_TOKENS | {"btPC"}:
        return plate_id
    return bed_label


def _patch_gcode(data: bytes, bed_label: str, plate_id: str) -> bytes:
    text = data.decode("utf-8", errors="replace")

    def replace(match: re.Match[str]) -> str:
        prefix = match.group(1)
        value = match.group(2).strip()
        lower_prefix = prefix.lower()
        if "plate_id" in lower_prefix:
            if plate_id:
                return f"{prefix}{plate_id}{match.group(3)}"
            return match.group(0)
        if "curr_bed_type" in lower_prefix or value in _PLATE_LABELS or value in _PLATE_TOKENS or value in _COOL_TAGS:
            chosen = _locked_plate_value(value, bed_label, plate_id)
            return f"{prefix}{chosen}{match.group(3)}"
        return match.group(0)

    text = _GCODE_BED.sub(replace, text)
    if plate_id and f"plate_id = {plate_id}" not in text:
        text = re.sub(
            r"(?m)^(\s*;\s*curr_bed_type\s*=.*)$",
            lambda match: f"{match.group(1)}\n; plate_id = {plate_id}",
            text,
            count=1,
        )
        if f"plate_id = {plate_id}" not in text:
            text = f"; plate_id = {plate_id}\n{text}"
    return text.encode("utf-8")


def _patch_config(data: bytes, bed_label: str, plate_id: str) -> bytes:
    text = data.decode("utf-8", errors="replace")
    text = _CURR_BED_JSON.sub(lambda match: _swap_plate(match, bed_label, plate_id), text)
    text = _CURR_BED_XML.sub(lambda match: _swap_plate(match, bed_label, plate_id), text)
    text = _BED_JSON.sub(lambda match: _replace_known_plate(match, bed_label, plate_id), text)
    text = _BED_XML.sub(lambda match: _replace_known_plate(match, bed_label, plate_id), text)
    if plate_id and 'key="plate_id"' not in text and "</plate>" in text:
        text = text.replace(
            "</plate>",
            f'<metadata key="plate_id" value="{plate_id}"/></plate>',
            1,
        )
    if plate_id and '"plate_id"' not in text and '"curr_bed_type"' in text:
        text = _CURR_BED_JSON.sub(
            lambda match: f'{match.group(0)}, "plate_id": "{plate_id}"',
            text,
            count=1,
        )
    return text.encode("utf-8")


def _swap_plate(match: re.Match[str], bed_label: str, plate_id: str) -> str:
    chosen = _locked_plate_value(match.group(2), bed_label, plate_id)
    return f"{match.group(1)}{chosen}{match.group(3)}"


def _replace_known_plate(match: re.Match[str], bed_label: str, plate_id: str) -> str:
    value = match.group(2)
    if value in _PLATE_LABELS or value in _PLATE_TOKENS or value in _COOL_TAGS:
        return _swap_plate(match, bed_label, plate_id)
    return match.group(0)


def _reject_cool_plate_tag(path: Path) -> None:
    """X1C dogfood refuses a sliced file whose selected plate is still cool_plate."""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            chunks = [
                archive.read(info.filename).decode("utf-8", errors="replace")
                for info in archive.infolist()
                if info.filename.lower().startswith("metadata/")
                and info.filename.lower().endswith((".gcode", ".json", ".config", ".xml"))
            ]
    except (OSError, zipfile.BadZipFile) as exc:
        raise BambuError("Could not check the plate tag in the sliced file.") from exc
    for value in _SELECTED_VALUE.findall("\n".join(chunks)):
        if value.strip() in _COOL_TAGS:
            raise BambuError(
                "Sliced file still tags the plate as cool_plate. "
                f"X1C dogfood locks {TEXTURED_PEI_PLATE_ID} ({ORCA_TEXTURED_PEI})."
            )


def _reject_separator(path: Path) -> None:
    text = str(path)
    if ";" in text or "\n" in text or "\r" in text:
        raise BambuError("A slicer preset path cannot contain a semicolon or a newline.")


def _unlink(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()


def _tail(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace").strip()
    if len(text) > 400:
        return text[-400:]
    return text


def _never(value: Never) -> Never:
    raise RuntimeError(f"Unhandled slicer: {value}")
