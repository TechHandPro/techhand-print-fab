"""Starting Bambu X1 Carbon / Orca notes. Not a slicer profile and not a print job."""

from __future__ import annotations

from typing import Any

DEFAULT_MATERIAL = "PLA"
DEFAULT_BED_TYPE = "textured_plate"
TARGET_PRINTER = "Bambu Lab X1 Carbon"
TARGET_NOZZLE_MM = 0.4
# Jeremiah's X1 Carbon plate is Textured PEI. Orca's enum string is
# "Textured PEI Plate". The sliced-file lock token is textured_pei.
# cool_plate is a different plate and must not be the dogfood tag.
TEXTURED_PEI_PLATE_ID = "textured_pei"
ORCA_TEXTURED_PEI = "Textured PEI Plate"

MATERIALS: dict[str, dict[str, Any]] = {
    "PLA": {
        "aliases": ("pla", "pla+", "pla-plus"),
        "nozzle_c": "220 start, 200–230 range",
        "bed_c": "55–65 on textured PEI. A cool plate can be lower; follow the spool datasheet.",
        "chamber": "Not required. Leave the door open if the part softens.",
        "fan": "High after the first layers, often near 100% for overhangs.",
        "speed": "Faster than PETG is normal. Drop speed if the corners lift or the layers look glossy and weak.",
        "drying": "Optional. Dry near 45–55 °C if the spool pops or strings.",
        "nozzle_hardware": "Brass is fine for unfilled PLA. Hardened if the grade is filled or glitter.",
        "adhesion": "Clean textured PEI. Wash the plate when the first layer lets go.",
        "watchouts": "PLA softens in a warm car or enclosure. Use PETG or ASA when the part has to stay stiff in heat.",
    },
    "PETG": {
        "aliases": ("petg",),
        "nozzle_c": "245 start, 230–250 range",
        "bed_c": "70–80",
        "chamber": "Optional. A closed door helps layer adhesion on larger parts.",
        "fan": "About 30–50% after the first layers. Too much fan weakens layers.",
        "speed": "Stay conservative, roughly 8–12 mm³/s volumetric, until the filament is dialed in.",
        "drying": "Dry near 65 °C for several hours if you see stringing or popping.",
        "nozzle_hardware": "Brass or hardened. Hardened is worth it if the filament is filled.",
        "adhesion": "Textured PEI is usually enough. Wash the plate when parts release early.",
        "watchouts": "Stringing and moisture show up before a temperature problem does.",
    },
    "ABS": {
        "aliases": ("abs",),
        "nozzle_c": "260 start, 250–270 range, follow the spool datasheet",
        "bed_c": "90–100",
        "chamber": "Keep the printer enclosed. Drafts lift corners.",
        "fan": "Low, about 0–20%, and only for bridges.",
        "speed": "Slower on the first layers. Let the chamber warm before a large part.",
        "drying": "Dry near 80 °C if the spool has been open.",
        "nozzle_hardware": "Brass is fine for unfilled ABS. Hardened if it is filled.",
        "adhesion": "Hot PEI and a brim on sharp corners.",
        "watchouts": "ABS fumes. Ventilate the room. A cold chamber is the usual cause of warp.",
    },
    "ASA": {
        "aliases": ("asa",),
        "nozzle_c": "260 start, 250–270 range",
        "bed_c": "90–100",
        "chamber": "Keep the printer enclosed. Drafts cause corners to lift.",
        "fan": "Low, about 0–20%, and only if the part needs it for bridges.",
        "speed": "Slower than PETG on the first layers. Let the chamber warm before a large part.",
        "drying": "Dry near 80 °C if the spool has been open.",
        "nozzle_hardware": "Brass is fine for unfilled ASA. Hardened if it is glass or fiber filled.",
        "adhesion": "Hot PEI, a brim on sharp corners, and no cold room air on the bed.",
        "watchouts": "ASA fumes. Ventilate the room. Do not treat a closed chamber as optional on this material.",
    },
    "TPU": {
        "aliases": ("tpu", "tpu95a", "tpu-95a"),
        "nozzle_c": "225 start for 95A, 220–240 range",
        "bed_c": "30–50",
        "chamber": "Not required. Leave the door open if the filament softens in the feed path.",
        "fan": "Low. High fan can weaken flexible layers.",
        "speed": "Slow. Direct drive is the reliable setup. Bowden feeds grind soft filament.",
        "drying": "Dry before a long part. Wet TPU foams and strings.",
        "nozzle_hardware": "Brass is fine. Avoid tight retraction.",
        "adhesion": "A clean PEI plate. Too much bed heat makes the first layer hard to remove.",
        "watchouts": "TPU is a poor choice for stiff structural parts. Design the flex into the shape.",
    },
    "PA": {
        "aliases": ("pa", "pa6", "pa12", "nylon"),
        "nozzle_c": "280 start, 270–300 range, follow the spool datasheet",
        "bed_c": "80–100",
        "chamber": "Enclosed, warmed, minimal drafts. Nylon warps when the core cools faster than the shell.",
        "fan": "Off or very low.",
        "speed": "Moderate. A wet spool prints worse than a cold nozzle.",
        "drying": "Dry thoroughly, often near 80 °C, and keep the spool dry while printing.",
        "nozzle_hardware": "Hardened if the grade is filled. Unfilled PA can use brass.",
        "adhesion": "Hot bed, brim, and a clean sheet. Release agent only if the datasheet asks for it.",
        "watchouts": "PA absorbs water in hours. Weigh the spool or dry again if layers foam.",
    },
    "PA-CF": {
        "aliases": ("pa-cf", "pacf", "pa6-cf", "nylon-cf", "pa-cf10"),
        "nozzle_c": "290 start, 280–310 range, follow the spool datasheet",
        "bed_c": "90–100",
        "chamber": "Enclosed. Fiber-filled nylon still warps if the chamber is cold.",
        "fan": "Off or very low.",
        "speed": "Slower than unfilled PA. Abrasive filament punishes a high flow rate.",
        "drying": "Dry harder than unfilled PA and print from a dry box when you can.",
        "nozzle_hardware": "Hardened nozzle required. A brass nozzle will wear out.",
        "adhesion": "Hot PEI and a brim. Fiber-filled parts can bond very hard to a smooth plate.",
        "watchouts": "Abrasive and hygroscopic. Plan on a hardened nozzle and a dry spool before tuning speed.",
    },
}


def material_list() -> str:
    """Human list of the allowlist. The dict above is the only source."""
    names = list(MATERIALS)
    if len(names) < 2:
        return ", ".join(names)
    return ", ".join(names[:-1]) + ", or " + names[-1]


def resolve_print_material(explicit: str, part_material: str = "") -> str:
    """Call material, then the part material, then PLA.

    An unknown explicit name returns an empty string so the caller can reject it.
    An unknown or empty part material falls through to PLA.
    """
    if explicit.strip():
        return canonical_material(explicit) or ""
    return canonical_material(part_material) or DEFAULT_MATERIAL


def canonical_material(name: str) -> str | None:
    text = name.strip().lower().replace("_", "-")
    if not text:
        return None
    for canonical, notes in MATERIALS.items():
        aliases = notes["aliases"]
        if text == canonical.lower() or text in aliases:
            return canonical
    return None


def profile_notes(material: str) -> dict[str, Any] | None:
    canonical = canonical_material(material)
    if canonical is None:
        return None
    notes = dict(MATERIALS[canonical])
    notes.pop("aliases", None)
    return {
        "material": canonical,
        "printer": TARGET_PRINTER,
        "nozzle_mm": TARGET_NOZZLE_MM,
        "slicer_family": "OrcaSlicer or Bambu Studio",
        "profile_applied": False,
        "sliced": False,
        "starting_notes": notes,
        "disclaimer": (
            f"Starting notes for a {TARGET_PRINTER} with a {TARGET_NOZZLE_MM:.1f} mm nozzle. "
            "Confirm them against the filament datasheet. "
            "These notes are not an official Bambu profile and they do not start a printer."
        ),
    }
