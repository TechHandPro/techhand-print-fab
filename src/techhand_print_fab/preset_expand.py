"""Flatten an installed Orca or Bambu profile tree into CLI-ready presets.

OrcaSlicer and Bambu Studio's CLI do not expand ``inherits``. A system preset
that only points at a parent is not enough, and a child that still names a
parent drops the parent keys. This module walks the user's installed
``resources/profiles`` tree, overlays child keys on the parent chain, and
writes ``machine.json``, ``process.json``, and ``filament/<MATERIAL>.json``
with ``inherits`` removed.

Vendor profiles are not shipped in this package. Nothing here calls the
Bambu cloud.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from techhand_print_fab.profiles import TARGET_NOZZLE_MM
from techhand_print_fab.store import default_data_dir

_MAX_PRESET_BYTES = 2_000_000
_MAX_FILES = 5000
_MAX_DEPTH = 8
CACHE_NAME = "x1c-0.4-pla-textured"
MACHINE_NAME = "Bambu Lab X1 Carbon 0.4 nozzle"
_PROCESS_NAMES = (
    "0.20mm Standard @BBL X1C",
    "0.20mm Standard @BBL X1C 0.4 nozzle",
)
_FILAMENT_NAMES: dict[str, tuple[str, ...]] = {
    "PLA": (
        "Bambu PLA Basic @BBL X1C",
        "Generic PLA @BBL X1C",
        "Bambu PLA Basic @BBL X1",
    ),
    "PETG": (
        "Bambu PETG Basic @BBL X1C",
        "Generic PETG @BBL X1C",
        "Bambu PETG Basic @BBL X1",
    ),
    "ABS": (
        "Bambu ABS @BBL X1C",
        "Generic ABS @BBL X1C",
        "Bambu ABS @BBL X1",
    ),
    "ASA": (
        "Bambu ASA @BBL X1C",
        "Generic ASA @BBL X1C",
        "Bambu ASA @BBL X1",
    ),
    "TPU": (
        "Bambu TPU 95A @BBL X1C",
        "Generic TPU @BBL X1C",
        "Bambu TPU 95A @BBL X1",
    ),
    "PA": (
        "Bambu PA @BBL X1C",
        "Generic PA @BBL X1C",
        "Generic Nylon @BBL X1C",
    ),
    "PA-CF": (
        "Bambu PA-CF @BBL X1C",
        "Generic PA-CF @BBL X1C",
        "Bambu PA-CF @BBL X1",
    ),
}
_PROFILE_RELATIVE = (
    ("resources", "profiles"),
    ("Resources", "profiles"),
    ("share", "OrcaSlicer", "resources", "profiles"),
    ("share", "orca-slicer", "resources", "profiles"),
    ("share", "BambuStudio", "resources", "profiles"),
    ("share", "bambu-studio", "resources", "profiles"),
)
PresetStatus = Literal["ok", "missing", "inherits", "invalid"]


class ExpandError(Exception):
    """The installed profile tree could not be flattened."""


@dataclass(frozen=True)
class PresetFileReport:
    file: str
    status: PresetStatus
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"file": self.file, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class _Node:
    name: str
    path: Path
    payload: dict[str, Any]
    instantiation: bool


def default_preset_dir() -> Path:
    """Cache for the standing X1 Carbon 0.4 / PLA / textured-plate presets."""
    return default_data_dir() / "slicer-presets" / CACHE_NAME


def preset_destination(explicit: str | None = None) -> tuple[Path | None, str]:
    """``FAB_SLICER_PRESETS`` when it is set, otherwise the local cache.

    A path that is not a directory is rejected. A missing directory is returned
    so the expander can create it.
    """
    raw = os.environ.get("FAB_SLICER_PRESETS", "") if explicit is None else explicit
    raw = raw.strip()
    if not raw:
        return default_preset_dir(), ""
    if len(raw) > 4096:
        return None, "FAB_SLICER_PRESETS is too long."
    path = Path(raw).expanduser()
    try:
        if path.is_symlink():
            return None, "FAB_SLICER_PRESETS is a symlink."
        resolved = path.resolve()
    except OSError:
        return None, "FAB_SLICER_PRESETS could not be resolved."
    if resolved.exists() and not resolved.is_dir():
        return None, "FAB_SLICER_PRESETS is not a directory."
    return resolved, ""


def find_profile_root(
    binary: Path | None = None,
    *,
    explicit: str | None = None,
) -> tuple[Path | None, str]:
    """Installed ``resources/profiles`` directory.

    ``FAB_SLICER_PROFILE_ROOT`` (or ``explicit``) wins. A bad explicit path is
    not replaced by a search next to the slicer binary.
    """
    raw = os.environ.get("FAB_SLICER_PROFILE_ROOT", "") if explicit is None else explicit
    raw = raw.strip()
    if raw:
        if len(raw) > 4096:
            return None, "FAB_SLICER_PROFILE_ROOT is too long."
        path = Path(raw).expanduser()
        try:
            if path.is_symlink():
                return None, "FAB_SLICER_PROFILE_ROOT is a symlink."
            resolved = path.resolve()
        except OSError:
            return None, "FAB_SLICER_PROFILE_ROOT could not be resolved."
        if _looks_like_profiles(resolved):
            return resolved, ""
        return None, (
            "FAB_SLICER_PROFILE_ROOT is not a resources/profiles directory "
            f"({resolved}). Expected BBL.json, BBL/machine, or machine/*.json."
        )
    found = _search_near_binary(binary)
    if found is not None:
        return found, ""
    hint = ""
    if binary is not None and "appimage" in binary.name.lower():
        hint = " An AppImage does not expose resources/profiles. Extract it or set FAB_SLICER_PROFILE_ROOT."
    return None, (
        "No Orca or Bambu resources/profiles directory was found next to the slicer."
        + hint
        + " Set FAB_SLICER_PROFILE_ROOT to that directory."
    )


def missing_reports(material: str) -> tuple[PresetFileReport, ...]:
    filament = f"filament/{material}.json"
    return (
        _missing("machine.json"),
        _missing("process.json"),
        _missing(filament),
    )


def inspect_presets(root: Path | None, material: str) -> tuple[PresetFileReport, ...]:
    """Status of the three CLI files. ``inherits`` is never treated as ready."""
    if root is None or not root.is_dir() or root.is_symlink():
        return missing_reports(material)
    return (
        _inspect_file(root, "machine.json", "machine", _machine_gap),
        _inspect_file(root, "process.json", "process", _process_gap),
        _inspect_filament(root, material),
    )


def presets_ready(reports: tuple[PresetFileReport, ...]) -> bool:
    return bool(reports) and all(item.status == "ok" for item in reports)


def format_incomplete(
    reports: tuple[PresetFileReport, ...],
    dest: Path | None,
    *,
    profile_root: Path | None,
    profile_note: str,
) -> str:
    shown = str(dest) if dest is not None else "(unset)"
    root_shown = str(profile_root) if profile_root is not None else "<profile-root>"
    out_shown = str(dest) if dest is not None else "<out>"
    lines = [
        "Slicer presets are not ready. OrcaSlicer and Bambu Studio's CLI do not expand inherits.",
        f"Expected directory (FAB_SLICER_PRESETS or the local cache): {shown}",
    ]
    lines.extend(f"{item.file}: {item.status}. {item.detail}" for item in reports)
    if profile_note:
        lines.append(profile_note)
    lines.append(
        "techhand-print-fab --expand-presets "
        f"--profile-root {root_shown} --out {out_shown}"
    )
    lines.append("This server does not ship Bambu or Orca vendor profiles and does not call the Bambu cloud.")
    return "\n".join(lines)


def ensure_presets(
    dest: Path,
    profile_root: Path,
    material: str,
    bed_label: str | None,
    *,
    force: bool,
) -> tuple[PresetFileReport, ...]:
    """Write roles that are missing, inherited, or invalid.

    ``force`` rewrites all three files. A role that is already full is left
    in place so a custom ``machine.json`` is not replaced on every slice.
    """
    if not _looks_like_profiles(profile_root):
        raise ExpandError(
            "The profile root is not a resources/profiles directory. "
            "Expected BBL.json, BBL/machine, or machine/*.json."
        )
    if dest.exists() and (dest.is_symlink() or not dest.is_dir()):
        raise ExpandError("Preset output is not a directory.")
    dest.mkdir(parents=True, exist_ok=True)
    index = _index_tree(profile_root)
    current = inspect_presets(dest, material)
    errors: dict[str, str] = {}
    for report in current:
        if report.status == "ok" and not force:
            continue
        try:
            payload = _build_role(index, report.file, material, bed_label)
            _write_json(dest, report.file, payload)
        except ExpandError as exc:
            errors[report.file] = str(exc)
    _write_notice(dest)
    inspected = inspect_presets(dest, material)
    if not errors:
        return inspected
    return tuple(
        PresetFileReport(item.file, "invalid", errors[item.file])
        if item.file in errors and item.status != "ok"
        else item
        for item in inspected
    )


def preset_paths(root: Path, material: str) -> tuple[Path, Path, Path]:
    machine = root / "machine.json"
    process = root / "process.json"
    filament = root / "filament" / f"{material}.json"
    if not filament.is_file():
        alternate = root / f"{material}.json"
        if alternate.is_file() and not alternate.is_symlink():
            filament = alternate
    return machine, process, filament


def align_process_bed(process: Path, bed_label: str | None) -> None:
    """Store the physical plate on a full process preset before the CLI runs.

    Stock Bambu X1 Carbon profiles often keep ``Cool Plate``. Passing
    ``--curr-bed-type`` is not enough: Orca still writes that plate into the
    sliced 3MF. The process file has to name the plate the printer is using.
    """
    if not bed_label:
        return
    if process.is_symlink() or not process.is_file():
        raise ExpandError("process.json is not a regular file.")
    try:
        loaded = json.loads(process.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExpandError("process.json is not JSON.") from exc
    if not isinstance(loaded, dict):
        raise ExpandError("process.json must be a JSON object.")
    if loaded.get("type") != "process":
        raise ExpandError('process.json needs "type": "process".')
    parent = _inherits_parent(loaded)
    if parent:
        raise ExpandError(
            f'process.json still has inherits "{parent}". The CLI does not expand inherits.'
        )
    if loaded.get("curr_bed_type") == bed_label:
        return
    loaded["curr_bed_type"] = bed_label
    _write_json(process.parent, "process.json", loaded)


def _build_role(
    index: dict[tuple[str, str], _Node],
    relative: str,
    material: str,
    bed_label: str | None,
) -> dict[str, Any]:
    if relative == "machine.json":
        node = _select(index, "machine", (MACHINE_NAME,), _machine_score)
        merged = _flatten(index, "machine", node)
        merged["type"] = "machine"
        merged["nozzle_diameter"] = [f"{TARGET_NOZZLE_MM:.1f}"]
        if _machine_gap(merged):
            raise ExpandError(f"Flattened {MACHINE_NAME} has no machine_start_gcode string.")
        return merged
    if relative == "process.json":
        node = _select(index, "process", _PROCESS_NAMES, _process_score)
        merged = _flatten(index, "process", node)
        merged["type"] = "process"
        if bed_label:
            merged["curr_bed_type"] = bed_label
        if _process_gap(merged):
            raise ExpandError(f"Flattened {node.name} has no layer_height.")
        return merged
    node = _select(index, "filament", _FILAMENT_NAMES.get(material, ()), lambda name: _filament_score(name, material))
    merged = _flatten(index, "filament", node)
    merged["type"] = "filament"
    if _filament_gap(merged):
        raise ExpandError(f"Flattened {node.name} has no nozzle_temperature.")
    return merged


def _select(
    index: dict[tuple[str, str], _Node],
    type_name: str,
    names: tuple[str, ...],
    score_of,
) -> _Node:
    for name in names:
        found = index.get((type_name, name.lower()))
        if found is not None:
            return found
    best: _Node | None = None
    best_score = 0
    for (kind, _key), node in index.items():
        if kind != type_name:
            continue
        score = score_of(node.name)
        if not isinstance(score, int):
            continue
        if score < 8:
            continue
        if best is None or score > best_score or (score == best_score and _prefer(node, best)):
            best = node
            best_score = score
    if best is None:
        wanted = ", ".join(names) if names else type_name
        raise ExpandError(f"No {type_name} preset matched {wanted}.")
    return best


def _flatten(index: dict[tuple[str, str], _Node], type_name: str, leaf: _Node) -> dict[str, Any]:
    chain: list[_Node] = []
    seen: set[tuple[str, str]] = set()
    current = leaf
    current_type = type_name
    while True:
        slot = (current_type, current.name.lower())
        if slot in seen:
            raise ExpandError(f'Preset "{leaf.name}" inherits in a cycle.')
        seen.add(slot)
        chain.append(current)
        parent_name = _inherits_parent(current.payload)
        if not parent_name:
            break
        parent = _lookup(index, current_type, parent_name)
        if parent is None:
            raise ExpandError(
                f'Preset "{current.name}" inherits "{parent_name}", which is not in the profile tree.'
            )
        current = parent
        current_type = str(parent.payload.get("type") or current_type)
    merged: dict[str, Any] = {}
    for node in reversed(chain):
        for key, value in node.payload.items():
            if key == "inherits":
                continue
            merged[key] = value
    merged.pop("inherits", None)
    return merged


def _lookup(index: dict[tuple[str, str], _Node], type_name: str, name: str) -> _Node | None:
    found = index.get((type_name, name.lower()))
    if found is not None:
        return found
    matches = [node for (kind, key), node in index.items() if key == name.lower() and kind == type_name]
    if len(matches) == 1:
        return matches[0]
    any_type = [node for (_kind, key), node in index.items() if key == name.lower()]
    if len(any_type) == 1:
        return any_type[0]
    return None


def _index_tree(root: Path) -> dict[tuple[str, str], _Node]:
    index: dict[tuple[str, str], _Node] = {}
    count = 0
    root_resolved = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        try:
            relative = current.resolve().relative_to(root_resolved)
        except (OSError, ValueError):
            dirnames.clear()
            continue
        if len(relative.parts) > _MAX_DEPTH:
            dirnames.clear()
            continue
        dirnames[:] = [name for name in dirnames if not (current / name).is_symlink()]
        for filename in filenames:
            if not filename.endswith(".json"):
                continue
            path = current / filename
            count += 1
            if count > _MAX_FILES:
                return index
            _index_file(index, root_resolved, path)
    return index


def _index_file(index: dict[tuple[str, str], _Node], root: Path, path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        return
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
        size = path.stat().st_size
    except (OSError, ValueError):
        return
    if size <= 0 or size > _MAX_PRESET_BYTES:
        return
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    if not isinstance(loaded, dict):
        return
    type_name = loaded.get("type")
    if type_name not in {"machine", "process", "filament"}:
        return
    name = loaded.get("name")
    label = name.strip() if isinstance(name, str) and name.strip() else path.stem
    node = _Node(label, path, loaded, _is_instantiation(loaded))
    _remember(index, type_name, label, node)
    if path.stem.lower() != label.lower():
        _remember(index, type_name, path.stem, node)


def _remember(index: dict[tuple[str, str], _Node], type_name: str, label: str, node: _Node) -> None:
    slot = (type_name, label.lower())
    current = index.get(slot)
    if current is None or _prefer(node, current):
        index[slot] = node


def _prefer(candidate: _Node, current: _Node) -> bool:
    if candidate.instantiation and not current.instantiation:
        return True
    if current.instantiation and not candidate.instantiation:
        return False
    candidate_bbl = "BBL" in candidate.path.parts
    current_bbl = "BBL" in current.path.parts
    return candidate_bbl and not current_bbl


def _machine_score(name: str) -> int:
    upper = name.upper()
    score = 0
    if "X1 CARBON" in upper:
        score += 5
    if "0.4" in name:
        score += 3
    return score


def _process_score(name: str) -> int:
    upper = name.upper()
    score = 0
    if "0.20" in name or "0.2MM" in upper:
        score += 4
    if "X1C" in upper or "X1 CARBON" in upper:
        score += 3
    if "STANDARD" in upper:
        score += 3
    return score


def _filament_score(name: str, material: str) -> int:
    upper = name.upper()
    if material == "PLA":
        if "PLA-CF" in upper or "PLA CF" in upper or "PLA" not in _bounded(upper, "PLA"):
            return 0
    elif material == "PA":
        if "PA-CF" in upper or "PA CF" in upper or "PACF" in upper:
            return 0
        if "PA" not in _bounded(upper, "PA") and "NYLON" not in upper:
            return 0
    elif material == "PA-CF":
        if "PA-CF" not in upper and "PA CF" not in upper and "PACF" not in upper:
            return 0
    elif material.upper() not in upper:
        return 0
    score = 8
    if "X1C" in upper or "X1 CARBON" in upper:
        score += 3
    if "BASIC" in upper:
        score += 1
    return score


def _bounded(text: str, token: str) -> str:
    """Return ``token`` when it appears as its own word, else an empty string."""
    start = 0
    while True:
        found = text.find(token, start)
        if found < 0:
            return ""
        before = found == 0 or not text[found - 1].isalnum()
        after_at = found + len(token)
        after = after_at == len(text) or not text[after_at].isalnum()
        if before and after:
            return token
        start = found + 1


def _inspect_filament(root: Path, material: str) -> PresetFileReport:
    relative = f"filament/{material}.json"
    primary = root / "filament" / f"{material}.json"
    if primary.exists() or primary.is_symlink():
        return _inspect_file(root, relative, "filament", _filament_gap)
    alternate = root / f"{material}.json"
    if alternate.is_symlink():
        return PresetFileReport(relative, "invalid", f"{material}.json is a symlink.")
    if alternate.is_file():
        report = _classify_path(alternate, relative, "filament", _filament_gap)
        if report.status == "ok":
            return PresetFileReport(relative, "ok", f"Using {material}.json in the preset root.")
        return report
    return _missing(relative)


def _inspect_file(root: Path, relative: str, expected_type: str, gap) -> PresetFileReport:
    path = root.joinpath(*relative.split("/"))
    if path.is_symlink():
        return PresetFileReport(relative, "invalid", f"{relative} is a symlink. Put a regular file in the preset directory.")
    if not path.is_file():
        return _missing(relative)
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return PresetFileReport(relative, "invalid", f"{relative} is outside the preset directory.")
    return _classify_path(path, relative, expected_type, gap)


def _classify_path(path: Path, relative: str, expected_type: str, gap) -> PresetFileReport:
    try:
        size = path.stat().st_size
    except OSError:
        return PresetFileReport(relative, "invalid", f"{relative} could not be read.")
    if size <= 0 or size > _MAX_PRESET_BYTES:
        return PresetFileReport(relative, "invalid", f"{relative} is empty or over 2 MB.")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return PresetFileReport(relative, "invalid", f"{relative} is not JSON.")
    if not isinstance(loaded, dict):
        return PresetFileReport(relative, "invalid", f"{relative} must be a JSON object.")
    if loaded.get("type") != expected_type:
        return PresetFileReport(relative, "invalid", f'{relative} needs "type": "{expected_type}".')
    parent = _inherits_parent(loaded)
    needed = gap(loaded)
    if parent:
        extra = f" It also {needed}." if needed else ""
        return PresetFileReport(
            relative,
            "inherits",
            f'{relative} still has inherits "{parent}". The CLI does not expand inherits.{extra}',
        )
    if needed:
        return PresetFileReport(relative, "invalid", f"{relative} {needed}.")
    return PresetFileReport(relative, "ok", f"{relative} is a full preset.")


def _missing(relative: str) -> PresetFileReport:
    return PresetFileReport(relative, "missing", f"{relative} is not a regular file.")


def _machine_gap(loaded: dict[str, Any]) -> str:
    start = loaded.get("machine_start_gcode")
    if isinstance(start, str) and start.strip():
        return ""
    return "needs a machine_start_gcode string"


def _process_gap(loaded: dict[str, Any]) -> str:
    height = loaded.get("layer_height")
    if isinstance(height, bool) or not isinstance(height, (int, float, str)) or not str(height).strip():
        return "needs layer_height"
    return ""


def _filament_gap(loaded: dict[str, Any]) -> str:
    if _has_nozzle_temperature(loaded):
        return ""
    return "needs nozzle_temperature"


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


def _inherits_parent(payload: dict[str, Any]) -> str:
    value = payload.get("inherits")
    if isinstance(value, str):
        return value.strip()
    if value:
        return "a parent preset"
    return ""


def _is_instantiation(payload: dict[str, Any]) -> bool:
    value = payload.get("instantiation")
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"


def _looks_like_profiles(path: Path) -> bool:
    if not path.is_dir() or path.is_symlink():
        return False
    marker = path / "BBL.json"
    if marker.is_file() and not marker.is_symlink():
        return True
    if (path / "BBL" / "machine").is_dir() and not (path / "BBL").is_symlink():
        return True
    machine = path / "machine"
    if not machine.is_dir() or machine.is_symlink():
        return False
    return any(item.is_file() and not item.is_symlink() and item.suffix == ".json" for item in machine.iterdir())


def _search_near_binary(binary: Path | None) -> Path | None:
    if binary is None:
        return None
    try:
        resolved = binary.resolve()
    except OSError:
        return None
    seen: set[Path] = set()
    for parent in list(resolved.parents)[:6]:
        if parent in seen:
            continue
        seen.add(parent)
        for parts in _PROFILE_RELATIVE:
            candidate = parent.joinpath(*parts)
            if _looks_like_profiles(candidate):
                try:
                    return candidate.resolve()
                except OSError:
                    continue
    return None


def _write_json(dest: Path, relative: str, payload: dict[str, Any]) -> None:
    path = dest.joinpath(*relative.split("/"))
    if path.is_symlink():
        raise ExpandError(f"{relative} is a symlink. Put a regular file in the preset directory.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.is_symlink():
        raise ExpandError(f"Refusing to write {relative} through a symlink.")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_notice(dest: Path) -> None:
    notice = dest / "README.txt"
    if notice.exists():
        return
    text = (
        "Generated on this machine from an installed OrcaSlicer or Bambu Studio profile tree.\n"
        "inherits has been removed so the slicer CLI can load these files.\n"
        "Do not commit them. This package does not ship Bambu or Orca vendor profiles.\n"
        "Layout: machine.json, process.json, filament/<MATERIAL>.json.\n"
    )
    try:
        notice.write_text(text, encoding="utf-8")
    except OSError:
        return
