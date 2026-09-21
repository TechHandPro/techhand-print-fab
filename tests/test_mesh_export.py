from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from techhand_print_fab.files3d import read_stl, write_3mf, write_binary_stl
from techhand_print_fab.mesh import box, cylinder, l_bracket, tube
from techhand_print_fab.spec import MAX_HOLES, SpecError, analytic_volume_mm3, normalize


def test_box_volume_and_stl_roundtrip(tmp_path: Path) -> None:
    mesh = box(10, 20, 30)
    assert mesh.signed_volume() == 6000
    path = tmp_path / "box.stl"
    count = write_binary_stl(mesh, path, name="box")
    assert count == 12
    data = path.read_bytes()
    assert len(data) == 84 + 12 * 50
    assert data[80:84] == (12).to_bytes(4, "little")
    loaded = read_stl(path)
    assert len(loaded.triangles) == 12
    assert abs(loaded.signed_volume() - 6000) < 1e-4


def test_round_parts_have_positive_volume() -> None:
    solid = cylinder(5, 10)
    assert solid.signed_volume() > 0
    expected = analytic_volume_mm3(normalize({"kind": "cylinder", "diameter_mm": 10, "height_mm": 10}))
    assert expected is not None
    assert abs(solid.signed_volume() - expected) / expected < 0.02

    hollow = tube(5, 3, 10)
    assert hollow.signed_volume() > 0
    tube_spec = normalize(
        {
            "kind": "tube",
            "outer_diameter_mm": 10,
            "inner_diameter_mm": 6,
            "height_mm": 10,
        }
    )
    tube_volume = analytic_volume_mm3(tube_spec)
    assert tube_volume is not None
    assert abs(hollow.signed_volume() - tube_volume) / tube_volume < 0.02


def test_l_bracket_volume_matches_analytic() -> None:
    spec = normalize(
        {
            "kind": "l_bracket",
            "length_mm": 40,
            "width_mm": 30,
            "height_mm": 25,
            "thickness_mm": 3,
        }
    )
    expected = analytic_volume_mm3(spec)
    assert expected == 40 * 30 * 3 + 40 * 3 * 22
    mesh = l_bracket(40, 30, 25, 3)
    assert expected is not None
    assert abs(mesh.signed_volume() - expected) < 1e-4


def test_stl_write_refuses_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    link = tmp_path / "out.stl"
    link.symlink_to(victim)
    with pytest.raises(ValueError, match="symlink"):
        write_binary_stl(box(1, 1, 1), link)
    assert victim.read_text(encoding="utf-8") == "keep"


def test_hole_list_is_capped() -> None:
    holes = [{"diameter_mm": 1.0, "x_mm": 1.0, "y_mm": 1.0} for _ in range(MAX_HOLES + 1)]
    with pytest.raises(SpecError, match="holes"):
        normalize(
            {
                "kind": "plate",
                "length_mm": 10,
                "width_mm": 10,
                "thickness_mm": 2,
                "holes": holes,
            }
        )


def test_3mf_package_contains_a_mesh(tmp_path: Path) -> None:
    path = tmp_path / "box.3mf"
    write_3mf(box(10, 20, 30), path, name="box")
    with zipfile.ZipFile(path) as package:
        names = set(package.namelist())
        assert "3D/3dmodel.model" in names
        assert "[Content_Types].xml" in names
        model = package.read("3D/3dmodel.model").decode()
    assert 'unit="millimeter"' in model
    assert model.count("<triangle ") == 12
    assert "toolpath" not in model.lower()
