"""Binary STL and geometry-only 3MF writers.

The 3MF is a core-spec mesh package. It is not a Bambu or Orca project file
and it does not contain a sliced toolpath.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from techhand_print_fab.mesh import Mesh, Vec3

_STL_HEADER = b"techhand-print-fab dry-fire mesh"


def write_binary_stl(mesh: Mesh, path: Path, name: str = "part") -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink(path)
    header = (_STL_HEADER + b" " + name.encode("ascii", errors="ignore"))[:80]
    header = header.ljust(80, b" ")
    chunks = [header, struct.pack("<I", len(mesh.triangles))]
    for i, j, k in mesh.triangles:
        a = mesh.vertices[i]
        b = mesh.vertices[j]
        c = mesh.vertices[k]
        normal = _unit_normal(a, b, c)
        chunks.append(struct.pack("<12fH", *normal, *a, *b, *c, 0))
    path.write_bytes(b"".join(chunks))
    return len(mesh.triangles)


def read_stl(path: Path) -> Mesh:
    data = path.read_bytes()
    if len(data) < 84:
        raise ValueError("STL is too small")
    count = struct.unpack_from("<I", data, 80)[0]
    if 84 + count * 50 == len(data):
        vertices: list[Vec3] = []
        triangles: list[tuple[int, int, int]] = []
        offset = 84
        for _ in range(count):
            floats = struct.unpack_from("<12f", data, offset)
            base = len(vertices)
            vertices.append((floats[3], floats[4], floats[5]))
            vertices.append((floats[6], floats[7], floats[8]))
            vertices.append((floats[9], floats[10], floats[11]))
            triangles.append((base, base + 1, base + 2))
            offset += 50
        return Mesh(tuple(vertices), tuple(triangles))
    if data.lstrip().lower().startswith(b"solid"):
        return _read_ascii_stl(data.decode("utf-8", errors="replace"))
    raise ValueError("Unrecognized STL")


def write_3mf(mesh: Mesh, path: Path, name: str = "part") -> None:
    """Write a core-spec 3MF mesh. No slicer settings and no printer job."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink(path)
    vertices = "\n".join(
        f'          <vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>'
        for x, y, z in mesh.vertices
    )
    triangles = "\n".join(
        f'          <triangle v1="{i}" v2="{j}" v3="{k}"/>' for i, j, k in mesh.triangles
    )
    model = f"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <metadata name="Application">techhand-print-fab</metadata>
  <metadata name="Title">{escape(name)}</metadata>
  <resources>
    <object id="1" type="model" name="{escape(name)}">
      <mesh>
        <vertices>
{vertices}
        </vertices>
        <triangles>
{triangles}
        </triangles>
      </mesh>
    </object>
  </resources>
  <build>
    <item objectid="1"/>
  </build>
</model>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", rels)
        package.writestr("3D/3dmodel.model", model)


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to write through symlink {path.name}")


def _unit_normal(a: Vec3, b: Vec3, c: Vec3) -> Vec3:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    length = (nx * nx + ny * ny + nz * nz) ** 0.5
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def _read_ascii_stl(text: str) -> Mesh:
    vertices: list[Vec3] = []
    triangles: list[tuple[int, int, int]] = []
    current: list[Vec3] = []
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 4 and parts[0].lower() == "vertex":
            current.append((float(parts[1]), float(parts[2]), float(parts[3])))
            if len(current) == 3:
                base = len(vertices)
                vertices.extend(current)
                triangles.append((base, base + 1, base + 2))
                current = []
    if not triangles:
        raise ValueError("ASCII STL contained no triangles")
    return Mesh(tuple(vertices), tuple(triangles))
