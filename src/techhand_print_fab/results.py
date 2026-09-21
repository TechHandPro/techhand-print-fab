"""Tool payloads. Every result says this process did not start a printer."""

from __future__ import annotations

from typing import Any

DRY_FIRE_NOTE = (
    "Design and file export only. No printer job was started. "
    "Treat the result as dry-fire training output."
)


def envelope(**payload: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "dry_fire": True,
        "printer_dispatched": False,
        "mode": "design_export",
        "note": DRY_FIRE_NOTE,
    }
    body.update(payload)
    return body


def ok(message: str, **payload: Any) -> dict[str, Any]:
    return envelope(ok=True, refused=False, message=message, **payload)


def failure(message: str, **payload: Any) -> dict[str, Any]:
    return envelope(ok=False, refused=False, message=message, **payload)
