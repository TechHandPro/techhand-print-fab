"""Resolve caller paths so tools cannot read or write outside allowed roots."""

from __future__ import annotations

import os
from pathlib import Path


def import_roots(extra: list[Path] | None = None) -> list[Path]:
    roots = list(extra or [])
    raw = os.environ.get("FAB_IMPORT_ROOTS", "")
    if raw.strip():
        for piece in raw.split(os.pathsep):
            if piece.strip():
                roots.append(Path(piece).expanduser())
    return roots


def export_roots(extra: list[Path] | None = None) -> list[Path]:
    roots = list(extra or [])
    raw = os.environ.get("FAB_EXPORT_ROOTS", "")
    if raw.strip():
        for piece in raw.split(os.pathsep):
            if piece.strip():
                roots.append(Path(piece).expanduser())
    return roots


def resolve_under(roots: list[Path], candidate: Path) -> Path | None:
    """Return the resolved path when it stays inside one root after symlink resolution."""
    try:
        resolved = candidate.expanduser().resolve()
    except OSError:
        return None
    for root in roots:
        try:
            root_resolved = root.expanduser().resolve()
        except OSError:
            continue
        if resolved == root_resolved or _is_relative_to(resolved, root_resolved):
            return resolved
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
