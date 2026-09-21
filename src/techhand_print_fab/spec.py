"""Normalize the params JSON into one model spec.

OpenSCAD is the primary emitter. The same spec feeds the CadQuery script,
the fallback mesh, DFM, and the BOM.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Never

Kind = Literal[
    "box",
    "cylinder",
    "tube",
    "plate",
    "l_bracket",
    "mount_plate",
    "custom_scad",
]

KINDS: tuple[Kind, ...] = (
    "box",
    "cylinder",
    "tube",
    "plate",
    "l_bracket",
    "mount_plate",
    "custom_scad",
)

MAX_DIM_MM = 2000.0
MAX_SCAD_CHARS = 1_000_000
MAX_HOLES = 256
_INCLUDE = re.compile(r"(?im)^\s*(include|use)\s*[<\"]|\bimport\s*\(")


class SpecError(ValueError):
    """The params JSON is not a model this server can build."""


@dataclass
class Hole:
    diameter_mm: float
    x_mm: float
    y_mm: float
    face: str


@dataclass
class ModelSpec:
    kind: Kind
    length_mm: float = 0.0
    width_mm: float = 0.0
    height_mm: float = 0.0
    thickness_mm: float = 0.0
    outer_diameter_mm: float = 0.0
    inner_diameter_mm: float = 0.0
    corner_radius_mm: float = 0.0
    holes: list[Hole] = field(default_factory=list)
    material: str = ""
    notes: str = ""
    overhang_deg: float | None = None
    clearance_mm: float | None = None
    wall_thickness_mm: float | None = None
    scad_body: str = ""

    def policy_text(self) -> str:
        return "\n".join([self.notes, self.material, self.scad_body])


def _never(kind: Never) -> Never:
    raise SpecError(f"Unsupported kind: {kind}")


def _num(data: dict[str, Any], *keys: str, default: float | None = None) -> float | None:
    for key in keys:
        if key not in data or data[key] is None:
            continue
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SpecError(f"{key} must be a number")
        if not math.isfinite(float(value)):
            raise SpecError(f"{key} must be finite")
        return float(value)
    return default


def _positive(name: str, value: float | None) -> float:
    if value is None:
        raise SpecError(f"{name} is required")
    if value <= 0 or value > MAX_DIM_MM:
        raise SpecError(f"{name} must be between 0 and {MAX_DIM_MM:g} mm")
    return value


def assert_scad_closed(source: str) -> None:
    """Block OpenSCAD file imports so a prompt cannot read arbitrary paths."""
    if len(source) > MAX_SCAD_CHARS:
        raise SpecError("OpenSCAD source is too large")
    if _INCLUDE.search(source):
        raise SpecError(
            "OpenSCAD include, use, and import() are disabled. Paste geometry inline."
        )


def normalize(params: dict[str, Any]) -> ModelSpec:
    if not isinstance(params, dict):
        raise SpecError("params must be a JSON object")
    raw_kind = params.get("kind", "")
    if not isinstance(raw_kind, str) or raw_kind not in KINDS:
        raise SpecError("params.kind must be one of: " + ", ".join(KINDS))
    kind: Kind = raw_kind  # noqa: assignment is narrowed by the membership test

    length = _num(params, "length_mm")
    width = _num(params, "width_mm", "depth_mm")
    height = _num(params, "height_mm")
    thickness = _num(params, "thickness_mm")
    outer = _num(params, "outer_diameter_mm", "diameter_mm")
    inner = _num(params, "inner_diameter_mm")
    radius = _num(params, "corner_radius_mm", default=0.0) or 0.0
    wall = _num(params, "wall_thickness_mm")
    overhang = _num(params, "overhang_deg")
    clearance = _num(params, "clearance_mm")

    material = params.get("material") or ""
    notes = params.get("notes") or ""
    scad_body = params.get("scad_body") or ""
    if not all(isinstance(item, str) for item in (material, notes, scad_body)):
        raise SpecError("material, notes, and scad_body must be strings")
    if scad_body:
        assert_scad_closed(scad_body)

    holes = _parse_holes(params.get("holes") or [])

    match kind:
        case "plate" | "mount_plate":
            length = _positive("length_mm", length)
            width = _positive("width_mm", width)
            thickness = _positive("thickness_mm", thickness if thickness is not None else height)
            height = thickness
        case "box":
            length = _positive("length_mm", length)
            width = _positive("width_mm", width)
            height = _positive("height_mm", height)
            thickness = thickness or 0.0
            if thickness < 0:
                raise SpecError("thickness_mm must be zero or positive")
        case "cylinder":
            outer = _positive("outer_diameter_mm", outer)
            height = _positive("height_mm", height)
            length = outer
            width = outer
        case "tube":
            outer = _positive("outer_diameter_mm", outer)
            inner = _positive("inner_diameter_mm", inner)
            height = _positive("height_mm", height)
            if inner >= outer:
                raise SpecError("inner_diameter_mm must be smaller than outer_diameter_mm")
            length = outer
            width = outer
            wall = (outer - inner) / 2.0
        case "l_bracket":
            length = _positive("length_mm", length)
            width = _positive("width_mm", width)
            height = _positive("height_mm", height)
            thickness = _positive("thickness_mm", thickness)
            if thickness >= height or thickness >= width:
                raise SpecError("thickness_mm must be smaller than height_mm and width_mm")
        case "custom_scad":
            length = length or 0.0
            width = width or 0.0
            height = height or 0.0
            thickness = thickness or 0.0
        case _ as unhandled:
            _never(unhandled)

    if radius < 0:
        raise SpecError("corner_radius_mm must be zero or positive")
    if kind in {"box", "plate", "mount_plate"} and radius:
        if radius >= min(length, width) / 2:
            raise SpecError("corner_radius_mm must be smaller than half the shortest plan side")

    if overhang is not None and not 0 <= overhang <= 90:
        raise SpecError("overhang_deg must be between 0 and 90")
    if clearance is not None and not 0 <= clearance <= 20:
        raise SpecError("clearance_mm must be between 0 and 20")

    _validate_holes(kind, holes, length, width, height, thickness, outer or 0.0)

    return ModelSpec(
        kind=kind,
        length_mm=length,
        width_mm=width,
        height_mm=height,
        thickness_mm=thickness or 0.0,
        outer_diameter_mm=outer or 0.0,
        inner_diameter_mm=inner or 0.0,
        corner_radius_mm=radius,
        holes=holes,
        material=material.strip(),
        notes=notes,
        overhang_deg=overhang,
        clearance_mm=clearance,
        wall_thickness_mm=wall,
        scad_body=scad_body,
    )


def _parse_holes(raw: object) -> list[Hole]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SpecError("holes must be a list")
    if len(raw) > MAX_HOLES:
        raise SpecError(f"holes supports at most {MAX_HOLES} entries")
    holes: list[Hole] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SpecError(f"holes[{index}] must be an object")
        diameter = _positive("diameter_mm", _num(item, "diameter_mm"))
        x_mm = _num(item, "x_mm", default=0.0)
        y_mm = _num(item, "y_mm", default=0.0)
        if x_mm is None or y_mm is None:
            raise SpecError(f"holes[{index}] needs x_mm and y_mm")
        face = item.get("face") or "base"
        if not isinstance(face, str):
            raise SpecError(f"holes[{index}].face must be a string")
        holes.append(Hole(diameter_mm=diameter, x_mm=x_mm, y_mm=y_mm, face=face.strip().lower()))
    return holes


def _validate_holes(
    kind: Kind,
    holes: list[Hole],
    length: float,
    width: float,
    height: float,
    thickness: float,
    outer: float,
) -> None:
    for index, hole in enumerate(holes):
        if hole.face not in {"base", "upright"}:
            raise SpecError(f"holes[{index}].face must be base or upright")
        if kind == "custom_scad":
            continue
        if kind == "tube":
            raise SpecError("tube holes are not supported; set inner_diameter_mm for the bore")
        if kind == "cylinder":
            if hole.face != "base":
                raise SpecError("cylinder holes in v1 are axial (face=base)")
            reach = math.hypot(hole.x_mm, hole.y_mm) + hole.diameter_mm / 2
            if reach >= outer / 2:
                raise SpecError(f"holes[{index}] does not fit inside the cylinder")
            continue
        if hole.face == "upright":
            if kind != "l_bracket":
                raise SpecError("upright holes are only supported on l_bracket")
            if not (0 < hole.x_mm < length and thickness < hole.y_mm < height):
                raise SpecError(f"holes[{index}] is outside the upright face")
            continue
        if not (0 <= hole.x_mm <= length and 0 <= hole.y_mm <= width):
            raise SpecError(f"holes[{index}] is outside the part footprint")


def bounding_box_mm(spec: ModelSpec) -> tuple[float, float, float] | None:
    match spec.kind:
        case "box" | "plate" | "mount_plate" | "l_bracket":
            return (spec.length_mm, spec.width_mm, spec.height_mm)
        case "cylinder" | "tube":
            return (spec.outer_diameter_mm, spec.outer_diameter_mm, spec.height_mm)
        case "custom_scad":
            if spec.length_mm > 0 and spec.width_mm > 0 and spec.height_mm > 0:
                return (spec.length_mm, spec.width_mm, spec.height_mm)
            return None
        case _ as unhandled:
            _never(unhandled)


def effective_wall_mm(spec: ModelSpec) -> float | None:
    if spec.wall_thickness_mm is not None and spec.wall_thickness_mm > 0:
        return spec.wall_thickness_mm
    match spec.kind:
        case "tube":
            return (spec.outer_diameter_mm - spec.inner_diameter_mm) / 2
        case "plate" | "mount_plate" | "l_bracket":
            return spec.thickness_mm
        case "box":
            return min(spec.length_mm, spec.width_mm, spec.height_mm)
        case "cylinder":
            return min(spec.outer_diameter_mm, spec.height_mm)
        case "custom_scad":
            return None
        case _ as unhandled:
            _never(unhandled)


def hole_depth_mm(spec: ModelSpec, hole: Hole) -> float:
    match spec.kind:
        case "l_bracket":
            return spec.thickness_mm
        case "plate" | "mount_plate":
            return spec.thickness_mm
        case "box" | "cylinder":
            return spec.height_mm
        case "tube" | "custom_scad":
            return 0.0
        case _ as unhandled:
            _never(unhandled)


def analytic_volume_mm3(spec: ModelSpec) -> float | None:
    match spec.kind:
        case "box":
            volume = spec.length_mm * spec.width_mm * spec.height_mm
        case "plate" | "mount_plate":
            volume = spec.length_mm * spec.width_mm * spec.thickness_mm
        case "cylinder":
            radius = spec.outer_diameter_mm / 2
            volume = math.pi * radius * radius * spec.height_mm
        case "tube":
            outer_r = spec.outer_diameter_mm / 2
            inner_r = spec.inner_diameter_mm / 2
            volume = math.pi * (outer_r * outer_r - inner_r * inner_r) * spec.height_mm
        case "l_bracket":
            base = spec.length_mm * spec.width_mm * spec.thickness_mm
            upright = spec.length_mm * spec.thickness_mm * (spec.height_mm - spec.thickness_mm)
            volume = base + upright
        case "custom_scad":
            return None
        case _ as unhandled:
            _never(unhandled)
    for hole in spec.holes:
        radius = hole.diameter_mm / 2
        volume -= math.pi * radius * radius * hole_depth_mm(spec, hole)
    return max(volume, 0.0)
