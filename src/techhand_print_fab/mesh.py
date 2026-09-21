"""Triangle meshes for the kinds OpenSCAD would also emit.

The fallback path lets CI export STL/3MF without an OpenSCAD binary. Boolean
holes and corner radii stay in the OpenSCAD source; this mesh is the solid
(a tube still includes its bore).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Never

from techhand_print_fab.spec import ModelSpec


class MeshError(ValueError):
    """The spec has no fallback mesh."""


Vec3 = tuple[float, float, float]
Tri = tuple[int, int, int]


@dataclass(frozen=True)
class Mesh:
    vertices: tuple[Vec3, ...]
    triangles: tuple[Tri, ...]

    def signed_volume(self) -> float:
        total = 0.0
        for i, j, k in self.triangles:
            ax, ay, az = self.vertices[i]
            bx, by, bz = self.vertices[j]
            cx, cy, cz = self.vertices[k]
            total += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
        return total / 6.0

    def bounds(self) -> tuple[Vec3, Vec3]:
        if not self.vertices:
            raise MeshError("empty mesh")
        xs = [vertex[0] for vertex in self.vertices]
        ys = [vertex[1] for vertex in self.vertices]
        zs = [vertex[2] for vertex in self.vertices]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def mesh_from_spec(spec: ModelSpec) -> Mesh:
    match spec.kind:
        case "box":
            return box(spec.length_mm, spec.width_mm, spec.height_mm)
        case "plate" | "mount_plate":
            return box(spec.length_mm, spec.width_mm, spec.thickness_mm)
        case "cylinder":
            return cylinder(spec.outer_diameter_mm / 2, spec.height_mm)
        case "tube":
            return tube(spec.outer_diameter_mm / 2, spec.inner_diameter_mm / 2, spec.height_mm)
        case "l_bracket":
            return l_bracket(spec.length_mm, spec.width_mm, spec.height_mm, spec.thickness_mm)
        case "custom_scad":
            raise MeshError("custom_scad has no fallback mesh; install OpenSCAD to export it")
        case _ as unhandled:
            _never(unhandled)


def _never(kind: Never) -> Never:
    raise MeshError(f"Unsupported kind: {kind}")


def box(length: float, width: float, height: float) -> Mesh:
    vertices: tuple[Vec3, ...] = (
        (0.0, 0.0, 0.0),
        (length, 0.0, 0.0),
        (length, width, 0.0),
        (0.0, width, 0.0),
        (0.0, 0.0, height),
        (length, 0.0, height),
        (length, width, height),
        (0.0, width, height),
    )
    triangles: tuple[Tri, ...] = (
        (0, 3, 1),
        (1, 3, 2),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (3, 7, 6),
        (3, 6, 2),
        (0, 4, 7),
        (0, 7, 3),
        (1, 2, 6),
        (1, 6, 5),
    )
    return Mesh(vertices, triangles)


def translate(mesh: Mesh, dx: float, dy: float, dz: float) -> Mesh:
    return Mesh(
        tuple((x + dx, y + dy, z + dz) for x, y, z in mesh.vertices),
        mesh.triangles,
    )


def concat(left: Mesh, right: Mesh) -> Mesh:
    offset = len(left.vertices)
    triangles = left.triangles + tuple(
        (i + offset, j + offset, k + offset) for i, j, k in right.triangles
    )
    return Mesh(left.vertices + right.vertices, triangles)


def l_bracket(length: float, width: float, height: float, thickness: float) -> Mesh:
    base = box(length, width, thickness)
    upright = translate(
        box(length, thickness, height - thickness),
        0.0,
        width - thickness,
        thickness,
    )
    return concat(base, upright)


def cylinder(radius: float, height: float, segments: int = 48) -> Mesh:
    vertices: list[Vec3] = [(0.0, 0.0, 0.0), (0.0, 0.0, height)]
    bottom: list[int] = []
    top: list[int] = []
    for index in range(segments):
        angle = 2 * math.pi * index / segments
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        bottom.append(len(vertices))
        vertices.append((x, y, 0.0))
        top.append(len(vertices))
        vertices.append((x, y, height))
    triangles: list[Tri] = []
    for index in range(segments):
        nxt = (index + 1) % segments
        triangles.append((bottom[index], bottom[nxt], top[nxt]))
        triangles.append((bottom[index], top[nxt], top[index]))
        triangles.append((0, bottom[nxt], bottom[index]))
        triangles.append((1, top[index], top[nxt]))
    return Mesh(tuple(vertices), tuple(triangles))


def tube(outer_r: float, inner_r: float, height: float, segments: int = 48) -> Mesh:
    if not 0 < inner_r < outer_r:
        raise MeshError("tube radii are invalid")
    vertices: list[Vec3] = []

    def ring(radius: float, z: float) -> list[int]:
        ids: list[int] = []
        for index in range(segments):
            angle = 2 * math.pi * index / segments
            ids.append(len(vertices))
            vertices.append((radius * math.cos(angle), radius * math.sin(angle), z))
        return ids

    outer_bottom = ring(outer_r, 0.0)
    outer_top = ring(outer_r, height)
    inner_bottom = ring(inner_r, 0.0)
    inner_top = ring(inner_r, height)
    triangles: list[Tri] = []
    for index in range(segments):
        nxt = (index + 1) % segments
        triangles.append((outer_bottom[index], outer_bottom[nxt], outer_top[nxt]))
        triangles.append((outer_bottom[index], outer_top[nxt], outer_top[index]))
        triangles.append((inner_bottom[index], inner_top[nxt], inner_bottom[nxt]))
        triangles.append((inner_bottom[index], inner_top[index], inner_top[nxt]))
        triangles.append((outer_bottom[index], inner_bottom[index], inner_bottom[nxt]))
        triangles.append((outer_bottom[index], inner_bottom[nxt], outer_bottom[nxt]))
        triangles.append((outer_top[index], outer_top[nxt], inner_top[nxt]))
        triangles.append((outer_top[index], inner_top[nxt], inner_top[index]))
    return Mesh(tuple(vertices), tuple(triangles))
