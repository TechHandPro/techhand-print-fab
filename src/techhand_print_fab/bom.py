"""Rough bill of materials from part metadata. Not a quote and not a sliced estimate."""

from __future__ import annotations

import math
from typing import Any

from techhand_print_fab.profiles import canonical_material, material_list
from techhand_print_fab.spec import ModelSpec, analytic_volume_mm3

# g/cm³, typical unfilled grades. Filled grades vary; the line says so.
_DENSITY_G_CM3: dict[str, float] = {
    "PLA": 1.24,
    "PETG": 1.27,
    "ABS": 1.04,
    "ASA": 1.07,
    "TPU": 1.21,
    "PA": 1.14,
    "PA-CF": 1.20,
}

_FILAMENT_DIAMETER_MM = 1.75

# Clearance-hole diameters and the fastener they usually match.
_CLEARANCE_HOLES: tuple[tuple[float, str], ...] = (
    (2.4, "M2"),
    (2.9, "M2.5"),
    (3.4, "M3"),
    (4.5, "M4"),
    (5.5, "M5"),
    (6.6, "M6"),
    (9.0, "M8"),
)


def bom_sketch(spec: ModelSpec) -> dict[str, Any]:
    volume = analytic_volume_mm3(spec)
    lines: list[dict[str, Any]] = []
    assumptions = [
        "Volumes are analytic from the params, with cylindrical holes subtracted.",
        "Holes that break out of an edge are still counted as full cylinders.",
        "No print time. A slicer has to estimate that.",
        "Filament length assumes 1.75 mm diameter.",
    ]
    if volume is None:
        assumptions.append("custom_scad has no analytic volume until dimensions are in params.")
        lines.append(
            {
                "item": "filament",
                "quantity": None,
                "unit": "g",
                "note": "Add a parametric kind or dimensions before a mass estimate.",
            }
        )
    else:
        lines.append(
            {
                "item": "solid volume",
                "quantity": round(volume, 2),
                "unit": "mm3",
                "note": "Analytic, before slicer infill. A sparse infill uses less filament.",
            }
        )
        material = canonical_material(spec.material) if spec.material else None
        if material is None:
            lines.append(
                {
                    "item": "filament mass",
                    "quantity": None,
                    "unit": "g",
                    "note": f"Set material to {material_list()} for a mass estimate.",
                }
            )
        else:
            mass = volume / 1000.0 * _DENSITY_G_CM3[material]
            lines.append(
                {
                    "item": f"{material} filament",
                    "quantity": round(mass, 2),
                    "unit": "g",
                    "note": f"Solid-equivalent mass at {_DENSITY_G_CM3[material]} g/cm³. Infill reduces this.",
                }
            )
        area = math.pi * (_FILAMENT_DIAMETER_MM / 2) ** 2
        lines.append(
            {
                "item": "1.75 mm filament length",
                "quantity": round(volume / area, 1),
                "unit": "mm",
                "note": "Solid-equivalent length. Infill and supports change it.",
            }
        )

    fastener_counts: dict[str, int] = {}
    for hole in spec.holes:
        name = _fastener_name(hole.diameter_mm)
        fastener_counts[name] = fastener_counts.get(name, 0) + 1
    for name, count in fastener_counts.items():
        lines.append(
            {
                "item": name,
                "quantity": count,
                "unit": "ea",
                "note": "Guess from clearance-hole diameter. Confirm the grip length yourself.",
            }
        )
    if spec.kind == "tube":
        lines.append(
            {
                "item": "bore",
                "quantity": round(spec.inner_diameter_mm, 2),
                "unit": "mm",
                "note": "Inner diameter. Not counted as a separate fastener.",
            }
        )

    return {"lines": lines, "assumptions": assumptions, "currency": None}


def _fastener_name(diameter_mm: float) -> str:
    best_name = ""
    best_delta = 999.0
    for clearance, name in _CLEARANCE_HOLES:
        delta = abs(clearance - diameter_mm)
        if delta < best_delta:
            best_delta = delta
            best_name = name
    if best_delta <= 0.35:
        return f"{best_name} screw"
    return f"pin or fastener for a {diameter_mm:.2f} mm hole"
