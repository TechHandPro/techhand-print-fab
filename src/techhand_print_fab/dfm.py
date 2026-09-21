"""FDM manufacturability notes for an X1 Carbon class machine.

These are heuristics on the parametric spec, not a slice and not a printer check.
"""

from __future__ import annotations

from typing import Any, Literal, Never

from techhand_print_fab.spec import (
    ModelSpec,
    bounding_box_mm,
    effective_wall_mm,
)

MIN_WALL_FAIL_MM = 0.8
MIN_WALL_WARN_MM = 1.2
MIN_HOLE_FAIL_MM = 0.8
MIN_HOLE_WARN_MM = 2.0
OVERHANG_WARN_DEG = 45.0
OVERHANG_FAIL_DEG = 70.0
CLEARANCE_FAIL_MM = 0.15
CLEARANCE_WARN_MM = 0.25
X1C_BUILD_MM = 256.0


def dfm_report(spec: ModelSpec) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    bbox = bounding_box_mm(spec)
    if bbox is None:
        findings.append(
            _finding(
                "info",
                "custom_geometry",
                "Custom OpenSCAD was not measured. Wall, hole, and bed checks need dimensions in params.",
            )
        )
    else:
        length, width, height = bbox
        if max(length, width, height) > X1C_BUILD_MM:
            findings.append(
                _finding(
                    "warn",
                    "bed_volume",
                    f"A modeled axis is over {X1C_BUILD_MM:g} mm, which is the X1 Carbon build length. "
                    "Reorient or split the part. This was not checked on a printer.",
                )
            )
        else:
            findings.append(
                _finding(
                    "info",
                    "bed_volume",
                    f"Bounding box {length:g} x {width:g} x {height:g} mm fits a 256 mm X1 Carbon axis.",
                )
            )

    wall = effective_wall_mm(spec)
    if wall is None:
        findings.append(
            _finding(
                "info",
                "wall",
                "No wall thickness was provided. Add thickness_mm or wall_thickness_mm.",
            )
        )
    elif wall < MIN_WALL_FAIL_MM:
        findings.append(
            _finding(
                "fail",
                "wall_too_thin",
                f"Thinnest modeled feature is {wall:.2f} mm. Below {MIN_WALL_FAIL_MM:g} mm is unreliable "
                "on a 0.4 mm nozzle.",
            )
        )
    elif wall < MIN_WALL_WARN_MM:
        findings.append(
            _finding(
                "warn",
                "wall_thin",
                f"Thinnest modeled feature is {wall:.2f} mm. Prefer at least {MIN_WALL_WARN_MM:g} mm "
                "(about three 0.4 mm perimeters).",
            )
        )
    else:
        findings.append(
            _finding("info", "wall", f"Thinnest modeled feature is {wall:.2f} mm.")
        )

    if not spec.holes and spec.kind != "tube":
        findings.append(_finding("info", "holes", "No holes were declared."))
    for index, hole in enumerate(spec.holes):
        if hole.diameter_mm < MIN_HOLE_FAIL_MM:
            level = "fail"
            code = "hole_too_small"
        elif hole.diameter_mm < MIN_HOLE_WARN_MM:
            level = "warn"
            code = "hole_small"
        else:
            level = "info"
            code = "hole"
        findings.append(
            _finding(
                level,
                code,
                f"Hole {index + 1} is {hole.diameter_mm:.2f} mm on the {hole.face} face. "
                "Printed holes finish smaller than drawn; drill or size up for a fastener.",
            )
        )
        if spec.kind == "l_bracket" and hole.face == "base" and hole.y_mm > spec.width_mm - spec.thickness_mm:
            findings.append(
                _finding(
                    "warn",
                    "hole_under_upright",
                    f"Base hole {index + 1} sits under the upright and may not be reachable.",
                )
            )

    if spec.kind == "tube":
        bore = spec.inner_diameter_mm
        level = "fail" if bore < MIN_HOLE_FAIL_MM else "warn" if bore < MIN_HOLE_WARN_MM else "info"
        findings.append(
            _finding(level, "bore", f"Tube bore is {bore:.2f} mm. Small bores close up when printed.")
        )

    if spec.overhang_deg is None:
        findings.append(
            _finding(
                "info",
                "overhang",
                "No overhang_deg was provided. Face angles were not measured from a mesh.",
            )
        )
    elif spec.overhang_deg > OVERHANG_FAIL_DEG:
        findings.append(
            _finding(
                "fail",
                "overhang_severe",
                f"Declared overhang is {spec.overhang_deg:g} degrees. Expect support or a reprint above "
                f"{OVERHANG_FAIL_DEG:g} degrees.",
            )
        )
    elif spec.overhang_deg > OVERHANG_WARN_DEG:
        findings.append(
            _finding(
                "warn",
                "overhang",
                f"Declared overhang is {spec.overhang_deg:g} degrees. The usual FDM limit without support "
                f"is about {OVERHANG_WARN_DEG:g} degrees.",
            )
        )
    else:
        findings.append(
            _finding("info", "overhang", f"Declared overhang is {spec.overhang_deg:g} degrees.")
        )

    if spec.kind == "l_bracket":
        findings.append(
            _finding(
                "info",
                "orientation",
                "An L bracket prints with fewer overhangs when one outer face is on the bed.",
            )
        )

    if spec.clearance_mm is None:
        findings.append(
            _finding(
                "info",
                "clearance",
                "No mating clearance_mm was provided. FDM fits often need about 0.3 mm.",
            )
        )
    elif spec.clearance_mm < CLEARANCE_FAIL_MM:
        findings.append(
            _finding(
                "fail",
                "clearance_tight",
                f"Clearance {spec.clearance_mm:.2f} mm is tight enough that mating faces may fuse.",
            )
        )
    elif spec.clearance_mm < CLEARANCE_WARN_MM:
        findings.append(
            _finding(
                "warn",
                "clearance_tight",
                f"Clearance {spec.clearance_mm:.2f} mm is on the tight side for PETG or ASA.",
            )
        )
    else:
        findings.append(
            _finding("info", "clearance", f"Clearance {spec.clearance_mm:.2f} mm is in a typical FDM range.")
        )

    if spec.corner_radius_mm > 0:
        findings.append(
            _finding(
                "info",
                "corner_radius",
                "Plan-view corner radius is in the OpenSCAD hull. The fallback mesh leaves those corners square.",
            )
        )

    summary = _summary(findings)
    return {
        "process": "fdm",
        "printer_class": "bambulab-x1c",
        "nozzle_assumption_mm": 0.4,
        "summary": summary,
        "findings": findings,
        "bounding_box_mm": None
        if bbox is None
        else {"x": bbox[0], "y": bbox[1], "z": bbox[2]},
    }


def _summary(findings: list[dict[str, str]]) -> str:
    levels = {item["level"] for item in findings}
    if "fail" in levels:
        return "fail"
    if "warn" in levels:
        return "warn"
    return "pass"


def _finding(level: Literal["fail", "warn", "info"], code: str, message: str) -> dict[str, str]:
    match level:
        case "fail" | "warn" | "info":
            return {"level": level, "code": code, "message": message}
        case _ as unhandled:
            _never(unhandled)


def _never(level: Never) -> Never:
    raise ValueError(f"Unknown finding level: {level}")
