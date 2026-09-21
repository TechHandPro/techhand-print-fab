"""Classify STL and 3MF files and write a Bambu Studio handoff for unsliced meshes."""

from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from techhand_print_fab.bambu_config import BambuError
from techhand_print_fab.bambu_frames import plate_pattern
from techhand_print_fab.paths import export_roots, resolve_under
from techhand_print_fab.profiles import (
    TARGET_NOZZLE_MM,
    TARGET_PRINTER,
    material_list,
    profile_notes,
)

MAX_MESH_BYTES = 200 * 1024 * 1024
_MAX_ZIP_ENTRIES = 400
_MAX_PLATE_UNCOMPRESSED = 32 * 1024 * 1024


@dataclass(frozen=True)
class SliceInfo:
    sliced: bool
    plates: tuple[int, ...]
    kind: str


def plate_numbers(path: Path) -> tuple[int, ...]:
    """Plate indexes that have Metadata/plate_N.gcode. Empty when the 3MF is geometry only."""
    return _plate_numbers(path)


def classify_mesh(path: Path) -> SliceInfo:
    name = path.name.lower()
    if name.endswith(".stl"):
        return SliceInfo(False, (), "stl")
    if not name.endswith(".3mf"):
        raise BambuError("Print file must be an .stl, .3mf, or .gcode.3mf.")
    plates = _plate_numbers(path)
    if plates:
        return SliceInfo(True, plates, "gcode_3mf")
    if name.endswith(".gcode.3mf"):
        raise BambuError("This .gcode.3mf has no Metadata/plate_N.gcode. It was not sent.")
    return SliceInfo(False, (), "geometry_3mf")


def resolve_mesh(
    file_path: str,
    *,
    part_dir: Path | None,
    project_dir: Path | None,
) -> Path:
    roots: list[Path] = []
    if part_dir is not None:
        roots.append(part_dir)
    if project_dir is not None:
        roots.append(project_dir)
    roots.extend(export_roots())
    if not file_path.strip():
        if part_dir is None:
            raise BambuError("Pass project_id and part_name, or file_path to a mesh under FAB_EXPORT_ROOTS.")
        chosen = default_model_file(part_dir)
        if chosen is None:
            raise BambuError("This part has no model.stl or model.3mf yet. Export it before queueing a print.")
        _check_size(chosen)
        return chosen.resolve()
    raw = Path(file_path).expanduser()
    bases: list[Path] = []
    if raw.is_absolute():
        bases.append(raw)
    else:
        if part_dir is not None:
            bases.append(part_dir / raw)
        bases.extend(root / raw for root in export_roots())
        if project_dir is not None:
            bases.append(project_dir / raw)
    for candidate in bases:
        resolved = resolve_under(roots, candidate)
        if resolved is not None and resolved.is_file():
            _check_size(resolved)
            return resolved
    raise BambuError("file_path is outside the part directory, the project directory, and FAB_EXPORT_ROOTS.")


def default_model_file(part_dir: Path) -> Path | None:
    """Prefer a sliced file unless a newer geometry mesh is sitting next to it."""
    gcode = part_dir / "model.gcode.3mf"
    meshes = [path for path in (part_dir / "model.3mf", part_dir / "model.stl") if _is_real_file(path)]
    gcode_ok = _is_real_file(gcode)
    if gcode_ok and meshes:
        newest = max(meshes, key=lambda path: path.stat().st_mtime_ns)
        if newest.stat().st_mtime_ns > gcode.stat().st_mtime_ns:
            return newest
        return gcode
    if gcode_ok:
        return gcode
    if meshes:
        return max(meshes, key=lambda path: path.stat().st_mtime_ns)
    return None


def write_handoff(
    source: Path,
    *,
    directory: Path,
    material_notes: dict[str, Any] | None,
    material: str,
    bed_type: str,
    detail: str,
) -> Path:
    if directory.is_symlink():
        raise BambuError("Refusing to write the Studio handoff through a symlink.")
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise BambuError("Studio handoff directory is not a real directory.")
    for child in directory.iterdir():
        if child.is_symlink() or (child.is_file() and child.name.startswith("model.")):
            child.unlink()
    if source.name.lower().endswith(".gcode.3mf"):
        target = directory / "model.gcode.3mf"
    elif source.suffix.lower() == ".3mf":
        target = directory / "model.3mf"
    else:
        target = directory / "model.stl"
    _write_bytes(target, source.read_bytes())
    notes_path = directory / "x1c-profile-notes.json"
    payload = {
        "material": material,
        "bed_type": bed_type,
        "printer": TARGET_PRINTER,
        "nozzle_mm": TARGET_NOZZLE_MM,
        "profile_applied": False,
        "sliced": False,
        "profile_notes": material_notes,
        "source": source.name,
        "fallback": detail,
    }
    _write_bytes(notes_path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    _write_bytes(
        directory / "HANDOFF.txt",
        _handoff_text(material_notes, material=material, bed_type=bed_type, detail=detail).encode("utf-8"),
    )
    return directory


def handoff_directory(part_dir: Path | None, source: Path) -> Path:
    if part_dir is not None:
        return part_dir / "studio-handoff"
    return source.parent / "studio-handoff"


def notes_for(material: str) -> dict[str, Any] | None:
    if not material.strip():
        return None
    return profile_notes(material)


def _plate_numbers(path: Path) -> tuple[int, ...]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) > _MAX_ZIP_ENTRIES:
                raise BambuError("3MF has too many entries.")
            plates: list[int] = []
            pattern = plate_pattern()
            for name in names:
                match = pattern.fullmatch(name.replace("\\", "/"))
                if match is None:
                    continue
                info = archive.getinfo(name)
                if info.file_size > _MAX_PLATE_UNCOMPRESSED:
                    raise BambuError("A plate gcode inside the 3MF is too large.")
                plates.append(int(match.group(1)))
    except zipfile.BadZipFile as exc:
        raise BambuError("3MF is not a zip file.") from exc
    return tuple(sorted(set(plates)))


def _check_size(path: Path) -> None:
    size = path.stat().st_size
    if size <= 0 or size > MAX_MESH_BYTES:
        raise BambuError("Print file is empty or over 200 MB.")


def _write_bytes(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise BambuError(f"Refusing to write through symlink {path.name}.")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o644)
    except OSError as exc:
        raise BambuError(f"Could not write {path.name}.") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def _is_real_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _handoff_text(
    material_notes: dict[str, Any] | None,
    *,
    material: str,
    bed_type: str,
    detail: str,
) -> str:
    material_line = (
        "Type the temperatures in x1c-profile-notes.json. Confirm them against the filament datasheet."
        if material_notes
        else f"No material was set, so there are no profile notes yet. Pass material as {material_list()}."
    )
    why = detail.strip() or "The headless slicer was not ready."
    return (
        "Bambu Studio handoff\n"
        "This file is geometry only. The X1 Carbon prints a sliced .gcode.3mf.\n"
        f"Target: {TARGET_PRINTER}, {TARGET_NOZZLE_MM:.1f} mm nozzle, "
        f"material {material or 'PLA'}, bed {bed_type}.\n"
        "\n"
        f"{why}\n"
        "\n"
        "Normal path when the CLI is installed: fab_slice, or fab_bambu_push_3mf, "
        "writes model.gcode.3mf without opening Bambu Studio.\n"
        "Install OrcaSlicer or Bambu Studio so orca-slicer or bambu-studio is on PATH "
        "(or set ORCA_SLICER_BIN / BAMBU_STUDIO_BIN). Export full printer, process, and "
        "filament JSON from that slicer. The CLI does not expand inherits, and this server "
        "does not download Bambu cloud profiles. Point FAB_SLICER_PRESETS at a directory with "
        "machine.json, process.json, and filament/PLA.json (or the material you named).\n"
        "\n"
        "Until that CLI is ready:\n"
        "1. Open the mesh in Bambu Studio or OrcaSlicer.\n"
        "2. Select the Bambu Lab X1 Carbon and a 0.4 mm nozzle.\n"
        f"3. {material_line}\n"
        "4. Slice and export a .gcode.3mf into this part directory or a directory on FAB_EXPORT_ROOTS.\n"
        "5. Call fab_bambu_push_3mf again with file_path set to that file, dry_run false, "
        "confirm true, and BAMBU_PRINT_ENABLED=1.\n"
        "\n"
        "These notes were not applied as a slicer profile. No printer job was submitted.\n"
    )
