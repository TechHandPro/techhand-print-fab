"""Load OpenSCAD that came from an allowed directory.

Relative include <file.scad> is inlined when the target stays inside an import
root. Absolute paths, parent-directory escapes, use, and import() stay refused.
Pasted scad_body still goes through assert_scad_closed and cannot include files.
"""

from __future__ import annotations

import re
from pathlib import Path

from techhand_print_fab.paths import resolve_under
from techhand_print_fab.spec import MAX_SCAD_CHARS, SpecError

_INCLUDE = re.compile(
    r"(?im)^[ \t]*(?P<kind>include|use)\s*[<\"](?P<target>[^>\"]+)[>\"]\s*;?"
)
_IMPORT_CALL = re.compile(r"\bimport\s*\(")
_ASSIGN = re.compile(
    r"(?m)^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>\d+(?:\.\d+)?)\s*;"
)
_WALL_NAMES = ("wall", "clamp_wall", "base_thick", "tab_t")
_CLEARANCE_NAMES = ("clearance", "tab_clear", "clear_d", "spring_id_clear")


def load_scad_tree_file(path: Path, roots: list[Path]) -> str:
    if path.suffix.lower() != ".scad":
        raise SpecError("source_path files must end in .scad")
    if path.stat().st_size > MAX_SCAD_CHARS:
        raise SpecError("OpenSCAD source is too large")
    text = path.read_text(encoding="utf-8")
    expanded = expand_includes(text, path.parent, roots, stack=())
    if len(expanded) > MAX_SCAD_CHARS:
        raise SpecError("OpenSCAD source is too large after include expansion")
    return expanded


def expand_includes(text: str, directory: Path, roots: list[Path], stack: tuple[Path, ...]) -> str:
    if _IMPORT_CALL.search(text):
        raise SpecError("OpenSCAD import() is disabled. Paste geometry or use include inside an import root.")
    if any(match.group("kind").lower() == "use" for match in _INCLUDE.finditer(text)):
        raise SpecError("OpenSCAD use is disabled. include of a file inside the import root is allowed.")

    def replace(match: re.Match[str]) -> str:
        raw = match.group("target").strip()
        target = _resolve_include(raw, directory, roots)
        if target in stack:
            raise SpecError(f"OpenSCAD include cycle at {target.name}")
        if target.stat().st_size > MAX_SCAD_CHARS:
            raise SpecError("OpenSCAD source is too large")
        body = target.read_text(encoding="utf-8")
        expanded = expand_includes(body, target.parent, roots, stack + (target,))
        return f"\n// begin {target.name}\n{expanded}\n// end {target.name}\n"

    return _INCLUDE.sub(replace, text)


def scad_parameter_hints(source: str) -> dict[str, float]:
    """Pull top-level millimeter assignments so DFM can speak about an imported part."""
    found: dict[str, float] = {}
    for match in _ASSIGN.finditer(source):
        found[match.group("name")] = float(match.group("value"))
    hints: dict[str, float] = {}
    walls = [found[name] for name in _WALL_NAMES if found.get(name, 0) > 0]
    if walls:
        hints["wall_thickness_mm"] = min(walls)
    clears = [found[name] for name in _CLEARANCE_NAMES if name in found and found[name] >= 0]
    if clears:
        hints["clearance_mm"] = min(clears)
    if all(name in found and found[name] > 0 for name in ("grip_len", "grip_w", "grip_h")):
        hints["length_mm"] = found["grip_len"]
        hints["width_mm"] = found["grip_w"]
        hints["height_mm"] = found["grip_h"]
    return hints


def _resolve_include(raw: str, directory: Path, roots: list[Path]) -> Path:
    if not raw or raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        raise SpecError("OpenSCAD include must be a relative file inside the import root")
    parts = Path(raw).parts
    if ".." in parts:
        raise SpecError("OpenSCAD include cannot climb out of the import root")
    candidate = (directory / raw).resolve()
    if resolve_under(roots, candidate) is None or not candidate.is_file():
        raise SpecError(f"OpenSCAD include {Path(raw).name} is outside the import root")
    return candidate
