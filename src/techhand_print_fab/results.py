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


def print_result(
    *,
    ok: bool,
    message: str,
    printer_dispatched: bool,
    mode: str,
    refused: bool = False,
    **payload: Any,
) -> dict[str, Any]:
    """Result for discovery and print push. Design tools keep using envelope()."""
    if printer_dispatched and not ok:
        raise RuntimeError("a failed print result cannot say the printer was dispatched")
    note = (
        "The printer or Farm Manager accepted this sliced job."
        if printer_dispatched
        else "No printer job was confirmed."
    )
    body: dict[str, Any] = {
        "ok": ok,
        "refused": refused,
        "dry_fire": not printer_dispatched,
        "printer_dispatched": printer_dispatched,
        "mode": mode,
        "note": note,
        "message": message,
    }
    body.update(payload)
    return body
