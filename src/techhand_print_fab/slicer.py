"""Headless slice through a local OrcaSlicer or Bambu Studio CLI.

The CLI is the same family Bambu Studio documents: ``--slice``, ``--export-3mf``,
``--load-settings``, and ``--load-filaments``. Full JSON presets are required
because that CLI does not expand ``inherits``. This module does not call the
Bambu cloud and does not ship a vendor profile.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Never

from techhand_print_fab.bambu_config import BambuError
from techhand_print_fab.print_files import MAX_MESH_BYTES, plate_numbers
from techhand_print_fab.profiles import (
    DEFAULT_BED_TYPE,
    DEFAULT_MATERIAL,
    TARGET_NOZZLE_MM,
    TARGET_PRINTER,
)

SLICE_TIMEOUT_S = 360
_MAX_PRESET_BYTES = 2_000_000
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
    "textured_plate": "Textured PEI Plate",
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


@dataclass(frozen=True)
class _Presets:
    machine: Path
    process: Path
    filament: Path


def fallback_help() -> str:
    """What to install when the CLI or the full presets are not ready."""
    return (
        "Headless slice needs OrcaSlicer or Bambu Studio's CLI "
        "(orca-slicer or bambu-studio on PATH, or ORCA_SLICER_BIN / BAMBU_STUDIO_BIN) "
        "and FAB_SLICER_PRESETS with full JSON exports: machine.json, process.json, "
        "and filament/<MATERIAL>.json. The CLI does not expand inherits. "
        "This server does not download Bambu cloud profiles. "
        f"Standing defaults are {DEFAULT_MATERIAL}, {DEFAULT_BED_TYPE}, "
        f"{TARGET_PRINTER}, {TARGET_NOZZLE_MM:.1f} mm nozzle. "
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
    root, root_problem = _preset_root()
    missing: list[str] = []
    problems: list[str] = []
    if binary is None:
        missing.append("orca-slicer or bambu-studio")
        problems.append("No OrcaSlicer or Bambu Studio CLI is on PATH.")
    elif binary.problem:
        missing.append("orca-slicer or bambu-studio")
        problems.append(binary.problem)
    if root is None:
        missing.append("FAB_SLICER_PRESETS")
        problems.append(root_problem)
    if missing or binary is None or root is None:
        return _handoff(tuple(missing), f"{' '.join(problems)} {fallback_help()}", "")
    presets, preset_problems = _load_presets(root, material)
    if presets is None:
        return _handoff(
            tuple(preset_problems),
            f"{' '.join(preset_problems)} {fallback_help()}",
            slicer_label(binary.family),
        )
    return _invoke(binary, source, output, presets, material=material, bed_type=bed_type)


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
        return _failed(label, f"{label} timed out after {SLICE_TIMEOUT_S} seconds.")
    except OSError as exc:
        _unlink(partial)
        return _failed(label, f"{label} could not be started ({exc.__class__.__name__}).")
    if completed.returncode != 0 or not partial.is_file() or partial.is_symlink():
        _unlink(partial)
        detail = _tail(completed.stderr or completed.stdout)
        suffix = f" {detail}" if detail else ""
        return _failed(label, f"{label} exited {completed.returncode} without a sliced file.{suffix}")
    if partial.stat().st_size <= 0 or partial.stat().st_size > MAX_MESH_BYTES:
        _unlink(partial)
        return _failed(label, f"{label} wrote an empty file or a file over 200 MB.")
    try:
        plates = plate_numbers(partial)
    except BambuError as exc:
        _unlink(partial)
        return _failed(label, f"{label} did not write a sliced 3MF ({exc}).")
    if not plates:
        _unlink(partial)
        return _failed(label, f"{label} did not write Metadata/plate_N.gcode.")
    if output.is_symlink() or (output.exists() and not output.is_file()):
        _unlink(partial)
        raise BambuError("Refusing to replace a sliced output that is not a regular file.")
    try:
        os.replace(partial, output)
    except OSError as exc:
        _unlink(partial)
        raise BambuError("Could not move the sliced file into place.") from exc
    return SliceAttempt(
        ok=True,
        mode="sliced",
        message=(
            f"{label} wrote {output.name} for {TARGET_PRINTER}, "
            f"{TARGET_NOZZLE_MM:.1f} mm nozzle, {material}, {bed_type}."
        ),
        output=output,
        slicer=label,
        profile_applied=True,
        missing=(),
    )


def _handoff(missing: tuple[str, ...], message: str, slicer: str) -> SliceAttempt:
    return SliceAttempt(
        ok=True,
        mode="studio_handoff",
        message=message,
        output=None,
        slicer=slicer,
        profile_applied=False,
        missing=missing,
    )


def _failed(slicer: str, message: str) -> SliceAttempt:
    return SliceAttempt(
        ok=False,
        mode="slice_error",
        message=f"{message} {fallback_help()}",
        output=None,
        slicer=slicer,
        profile_applied=False,
        missing=(),
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


def _preset_root() -> tuple[Path | None, str]:
    raw = os.environ.get("FAB_SLICER_PRESETS", "").strip()
    if not raw:
        return None, "FAB_SLICER_PRESETS is unset."
    if len(raw) > 4096:
        return None, "FAB_SLICER_PRESETS is too long."
    path = Path(raw).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        return None, "FAB_SLICER_PRESETS could not be resolved."
    if not resolved.is_dir():
        return None, "FAB_SLICER_PRESETS is not a directory."
    return resolved, ""


def _load_presets(root: Path, material: str) -> tuple[_Presets | None, list[str]]:
    problems: list[str] = []
    machine = _regular_file(root, "machine.json")
    process = _regular_file(root, "process.json")
    filament = _regular_file(root, f"filament/{material}.json")
    if filament is None:
        filament = _regular_file(root, f"{material}.json")
    if machine is None:
        problems.append("machine.json is missing (regular file, not a symlink).")
    else:
        problems.extend(_check_machine(machine))
    if process is None:
        problems.append("process.json is missing (regular file, not a symlink).")
    else:
        problems.extend(_check_process(process))
    if filament is None:
        problems.append(f"filament/{material}.json is missing (regular file, not a symlink).")
    else:
        problems.extend(_check_filament(filament, material))
    if problems or machine is None or process is None or filament is None:
        return None, problems
    return _Presets(machine, process, filament), []


def _check_machine(path: Path) -> list[str]:
    loaded, problem = _read_preset(path, "machine")
    if loaded is None:
        return [problem]
    start = loaded.get("machine_start_gcode")
    if isinstance(start, str) and start.strip():
        return []
    hint = ""
    if loaded.get("inherits"):
        hint = " The CLI does not expand inherits. Export the full preset from the slicer."
    return [f"machine.json needs a machine_start_gcode string.{hint}"]


def _check_process(path: Path) -> list[str]:
    loaded, problem = _read_preset(path, "process")
    if loaded is None:
        return [problem]
    height = loaded.get("layer_height")
    if isinstance(height, bool) or not isinstance(height, (int, float, str)) or not str(height).strip():
        return ["process.json needs layer_height."]
    return []


def _check_filament(path: Path, material: str) -> list[str]:
    loaded, problem = _read_preset(path, "filament")
    if loaded is None:
        return [problem]
    if _has_nozzle_temperature(loaded):
        return []
    hint = ""
    if loaded.get("inherits"):
        hint = " The CLI does not expand inherits. Export the full filament preset."
    return [f"filament/{material}.json needs nozzle_temperature.{hint}"]


def _read_preset(path: Path, expected_type: str) -> tuple[dict[str, Any] | None, str]:
    try:
        size = path.stat().st_size
    except OSError:
        return None, f"{path.name} could not be read."
    if size <= 0 or size > _MAX_PRESET_BYTES:
        return None, f"{path.name} is empty or over 2 MB."
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, f"{path.name} is not JSON."
    if not isinstance(loaded, dict):
        return None, f"{path.name} must be a JSON object."
    if loaded.get("type") != expected_type:
        return None, f'{path.name} needs "type": "{expected_type}".'
    return loaded, ""


def _has_nozzle_temperature(loaded: dict[str, Any]) -> bool:
    for key in ("nozzle_temperature", "nozzle_temperature_initial_layer"):
        value = loaded.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and any(_temperature_item(item) for item in value):
            return True
    return False


def _temperature_item(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    return isinstance(value, str) and bool(value.strip())


def _regular_file(root: Path, relative: str) -> Path | None:
    candidate = root.joinpath(*relative.split("/"))
    if candidate.is_symlink() or not candidate.is_file():
        return None
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate


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
