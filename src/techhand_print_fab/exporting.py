"""Mesh export. OpenSCAD when it is installed, otherwise the fallback mesh."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from techhand_print_fab.files3d import read_stl, write_3mf, write_binary_stl
from techhand_print_fab.mesh import Mesh, MeshError, mesh_from_spec
from techhand_print_fab.spec import ModelSpec

OPENSCAD_TIMEOUT_S = 180


class ExportError(ValueError):
    """The mesh could not be written."""


@dataclass(frozen=True)
class BuiltMesh:
    mesh: Mesh
    source: str
    warning: str
    booleans_omitted: bool


def openscad_executable() -> str | None:
    return shutil.which(os.environ.get("OPENSCAD_BIN", "openscad"))


def build_mesh(spec: ModelSpec, scad_path: Path) -> BuiltMesh:
    warning = ""
    binary = openscad_executable()
    if scad_path.is_file() and binary:
        stl_path = scad_path.with_name("_openscad.stl")
        ok, detail = run_openscad(binary, scad_path, stl_path)
        if ok:
            return BuiltMesh(
                mesh=read_stl(stl_path),
                source="openscad",
                warning="",
                booleans_omitted=False,
            )
        warning = f"OpenSCAD failed ({detail}). "
    if spec.kind == "custom_scad":
        raise ExportError(
            "custom_scad needs OpenSCAD on PATH to export a mesh. "
            "The .scad file is saved. No printer job was started."
        )
    try:
        mesh = mesh_from_spec(spec)
    except MeshError as exc:
        raise ExportError(str(exc)) from exc
    omitted = bool(spec.holes) or spec.corner_radius_mm > 0
    if omitted:
        warning += (
            "Fallback mesh omitted boolean holes and corner radii. "
            "Those features are still in the OpenSCAD file."
        )
    elif not binary:
        warning += "OpenSCAD is not installed. Exported the built-in mesh for this kind."
    return BuiltMesh(mesh=mesh, source="fallback_mesh", warning=warning.strip(), booleans_omitted=omitted)


def run_openscad(binary: str, scad_path: Path, stl_path: Path) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [binary, "-o", str(stl_path), scad_path.name],
            cwd=str(scad_path.parent),
            capture_output=True,
            timeout=OPENSCAD_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)[:500]
    if completed.returncode != 0 or not stl_path.is_file():
        err = completed.stderr.decode("utf-8", errors="replace").strip()
        return False, (err or "OpenSCAD exited without an STL")[:500]
    return True, binary


def write_mesh_file(mesh: Mesh, path: Path, kind: str, name: str) -> int:
    if kind == "stl":
        return write_binary_stl(mesh, path, name=name)
    if kind == "3mf":
        write_3mf(mesh, path, name=name)
        return len(mesh.triangles)
    raise ExportError(f"Unknown export kind: {kind}")
