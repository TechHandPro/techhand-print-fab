"""Dogfood path for the bundled trainer grip CAD v0 set."""

from __future__ import annotations

from pathlib import Path

import pytest

from techhand_print_fab.paths import bundled_cad_v0
from tests.conftest import tool_payload

EXPECTED_SLUGS = {
    "trainer-assembly-preview",
    "trainer-backstrap-insert",
    "trainer-grip-shell",
    "trainer-grip-shell-left",
    "trainer-grip-shell-right",
    "trainer-laser-clamp",
    "trainer-spring-seat",
    "trainer-trigger-lever",
}


def test_bundle_has_the_eight_trainer_files() -> None:
    names = {path.name for path in bundled_cad_v0().glob("*.scad")}
    assert names == {
        "assembly_preview.scad",
        "backstrap_insert.scad",
        "grip_shell.scad",
        "grip_shell_left.scad",
        "grip_shell_right.scad",
        "laser_clamp.scad",
        "spring_seat.scad",
        "trigger_lever.scad",
    }


def test_import_list_dfm_and_stl_dependency(server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    created = tool_payload(
        server,
        "fab_create_project",
        {
            "name": "Trainer grip v0",
            "description": "Original trainer grip block from the bundled CAD v0 set.",
        },
    )
    imported = tool_payload(
        server,
        "fab_param_model",
        {
            "project_id": created["project_id"],
            "part_name": "trainer",
            "source_path": "cad-v0",
            "params": {"material": "PETG"},
        },
    )
    assert imported["ok"] is True
    assert imported["refused"] is False
    assert {part["slug"] for part in imported["parts"]} == EXPECTED_SLUGS

    listed = tool_payload(server, "fab_list_parts", {"project_id": created["project_id"]})
    assert {part["slug"] for part in listed["parts"]} == EXPECTED_SLUGS

    shell = next(part for part in imported["parts"] if part["slug"] == "trainer-grip-shell")
    shell_scad = Path(shell["files"]["openscad"]).read_text(encoding="utf-8")
    assert "grip_shell_half" in shell_scad
    assert "TRAINER" in shell_scad

    left = next(part for part in imported["parts"] if part["slug"] == "trainer-grip-shell-left")
    left_scad = Path(left["files"]["openscad"]).read_text(encoding="utf-8")
    assert "grip_shell_half" in left_scad
    assert left_scad.rstrip().endswith("side = 1;")

    right = next(part for part in imported["parts"] if part["slug"] == "trainer-grip-shell-right")
    right_scad = Path(right["files"]["openscad"]).read_text(encoding="utf-8")
    assert right_scad.rstrip().endswith("side = -1;")

    dfm = tool_payload(
        server,
        "fab_dfm_check",
        {"project_id": created["project_id"], "part_name": "trainer-grip-shell"},
    )
    assert dfm["ok"] is True
    assert dfm["printer_dispatched"] is False
    assert dfm["summary"] == "pass"
    assert dfm["bounding_box_mm"] == {"x": 110.0, "y": 32.0, "z": 120.0}
    wall = next(item for item in dfm["findings"] if item["code"] == "wall")
    assert "2.40 mm" in wall["message"]

    clamp = tool_payload(
        server,
        "fab_dfm_check",
        {"project_id": created["project_id"], "part_name": "trainer-laser-clamp"},
    )
    assert clamp["summary"] == "warn"
    assert any(item["code"] == "clearance_tight" for item in clamp["findings"])

    exported = tool_payload(
        server,
        "fab_export_stl",
        {"project_id": created["project_id"], "part_name": "trainer-grip-shell"},
    )
    assert exported["ok"] is False
    assert exported["printer_dispatched"] is False
    assert "OpenSCAD" in exported["message"]
    assert "no printer job" in exported["message"].lower()
    assert Path(shell["files"]["openscad"]).is_file()
