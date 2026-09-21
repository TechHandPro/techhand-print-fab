"""Starting Bambu X1 Carbon / Orca notes. Not a slicer profile and not a print job."""

from __future__ import annotations

from typing import Any

MATERIALS: dict[str, dict[str, Any]] = {
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
        "printer": "Bambu Lab X1 Carbon",
        "slicer_family": "OrcaSlicer or Bambu Studio",
        "profile_applied": False,
        "sliced": False,
        "starting_notes": notes,
        "disclaimer": (
            "Starting notes for a human to type into a slicer. "
            "Confirm them against the filament datasheet. "
            "This server did not slice a file and did not start a printer."
        ),
    }
