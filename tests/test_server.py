from __future__ import annotations

import ast
import json
import zipfile
from pathlib import Path

import pytest

from techhand_print_fab.bridges import BridgeNotInstalled
from techhand_print_fab.cli import build_parser
from techhand_print_fab.files3d import write_binary_stl
from techhand_print_fab.mesh import box
from techhand_print_fab.server import INSTRUCTIONS, create_server
from tests.conftest import tool_names, tool_payload

CORE_TOOLS = {
    "fab_create_project",
    "fab_list_parts",
    "fab_param_model",
    "fab_export_stl",
    "fab_export_3mf",
    "fab_dfm_check",
    "fab_x1c_profile_notes",
    "fab_bom_sketch",
    "fab_bambu_discover",
    "fab_bambu_status",
    "fab_bambu_push_3mf",
}

REQUIRED = {
    "fab_create_project": {"name"},
    "fab_list_parts": {"project_id"},
    "fab_param_model": {"project_id", "part_name", "params"},
    "fab_export_stl": {"project_id", "part_name"},
    "fab_export_3mf": {"project_id", "part_name"},
    "fab_dfm_check": {"project_id", "part_name"},
    "fab_x1c_profile_notes": {"material"},
    "fab_bom_sketch": {"project_id", "part_name"},
    "fab_bambu_discover": set(),
    "fab_bambu_status": set(),
    "fab_bambu_push_3mf": set(),
}


def test_tool_schemas_and_instructions(server) -> None:
    tools = tool_names(server)
    assert set(tools) == CORE_TOOLS
    assert "fab_attach_to_ticket" not in tools
    for name, required in REQUIRED.items():
        schema = tools[name].input_schema
        assert schema["type"] == "object"
        assert required <= set(schema.get("required") or [])
    assert "proprietary" in INSTRUCTIONS.lower()
    assert "dry-fire" in INSTRUCTIONS.lower()
    assert "developer mode" in INSTRUCTIONS.lower()
    assert "weapon" in INSTRUCTIONS.lower()
    assert server.instructions == INSTRUCTIONS


def test_http_app_is_available(server) -> None:
    app = server.streamable_http_app()
    assert app is not None


def test_cli_defaults_to_stdio_on_localhost() -> None:
    args = build_parser().parse_args([])
    assert args.http is False
    assert args.host == "127.0.0.1"
    assert args.port == 8765


def test_tnt_flag_without_extra_fails() -> None:
    with pytest.raises(BridgeNotInstalled):
        create_server(enable_tnt=True)


def test_refuse_clone_does_not_write_a_part(server, isolated_store) -> None:
    created = tool_payload(server, "fab_create_project", {"name": "Fixture", "description": "mine"})
    assert created["ok"] is True
    refused = tool_payload(
        server,
        "fab_param_model",
        {
            "project_id": created["project_id"],
            "part_name": "copy",
            "intent": "Make an exact copy of the commercial product",
            "params": {"kind": "box", "length_mm": 10, "width_mm": 10, "height_mm": 10},
        },
    )
    assert refused["ok"] is False
    assert refused["refused"] is True
    assert refused["policy"] == "proprietary_clone"
    assert refused["printer_dispatched"] is False
    parts = tool_payload(server, "fab_list_parts", {"project_id": created["project_id"]})
    assert parts["parts"] == []
    assert not any((isolated_store.root / "projects").rglob("model.scad"))


def test_proprietary_clone_flag_is_refused(server) -> None:
    refused = tool_payload(
        server,
        "fab_create_project",
        {"name": "Bracket", "reproduction": "proprietary_clone"},
    )
    assert refused["refused"] is True
    assert refused["dry_fire"] is True


def test_original_part_exports_stl_and_3mf(server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    created = tool_payload(
        server,
        "fab_create_project",
        {"name": "Grip fixture", "description": "Original dimensions from my calipers."},
    )
    modeled = tool_payload(
        server,
        "fab_param_model",
        {
            "project_id": created["project_id"],
            "part_name": "Pad",
            "intent": "PETG mounting bracket for a 2020 extrusion, original dimensions from my calipers.",
            "params": {
                "kind": "box",
                "length_mm": 10,
                "width_mm": 20,
                "height_mm": 30,
                "material": "PETG",
                "holes": [{"diameter_mm": 3.4, "x_mm": 5, "y_mm": 10}],
            },
        },
    )
    assert modeled["ok"] is True
    assert modeled["primary_backend"] == "openscad"
    scad = Path(modeled["parts"][0]["files"]["openscad"]).read_text()
    script = Path(modeled["parts"][0]["files"]["cadquery"]).read_text()
    assert "cube([10, 20, 30])" in scad
    assert "cylinder" in scad
    ast.parse(script)
    assert "import cadquery as cq" in script

    stl = tool_payload(
        server,
        "fab_export_stl",
        {"project_id": created["project_id"], "part_name": "pad"},
    )
    assert stl["dry_fire"] is True
    assert stl["printer_dispatched"] is False
    assert "no printer job" in stl["message"].lower()
    assert stl["geometry"] == "fallback_mesh"
    assert stl["booleans_omitted"] is True
    assert stl["triangle_count"] == 12
    stl_bytes = Path(stl["path"]).read_bytes()
    assert len(stl_bytes) == 84 + 12 * 50

    mesh_3mf = tool_payload(
        server,
        "fab_export_3mf",
        {"project_id": created["project_id"], "part_name": "pad"},
    )
    assert mesh_3mf["slicer_project"] is False
    assert "3mf" in mesh_3mf["message"].lower()
    assert zip_has_model(Path(mesh_3mf["path"]))

    dfm = tool_payload(
        server,
        "fab_dfm_check",
        {"project_id": created["project_id"], "part_name": "pad"},
    )
    assert dfm["summary"] in {"pass", "warn", "info"}
    assert dfm["printer_dispatched"] is False
    assert any(item["code"] == "hole" for item in dfm["findings"])

    bom = tool_payload(
        server,
        "fab_bom_sketch",
        {"project_id": created["project_id"], "part_name": "pad"},
    )
    mass = next(line for line in bom["lines"] if line["item"] == "PETG filament")
    hole_volume = 3.141592653589793 * (1.7**2) * 30
    expected = (6000 - hole_volume) / 1000 * 1.27
    assert mass["quantity"] == pytest.approx(expected, abs=0.02)
    fastener = next(line for line in bom["lines"] if "M3" in line["item"])
    assert fastener["quantity"] == 1


def test_thin_wall_fails_dfm(server) -> None:
    created = tool_payload(server, "fab_create_project", {"name": "Sheet"})
    tool_payload(
        server,
        "fab_param_model",
        {
            "project_id": created["project_id"],
            "part_name": "shim",
            "params": {"kind": "plate", "length_mm": 20, "width_mm": 20, "thickness_mm": 0.4},
        },
    )
    dfm = tool_payload(
        server,
        "fab_dfm_check",
        {"project_id": created["project_id"], "part_name": "shim"},
    )
    assert dfm["summary"] == "fail"
    assert any(item["code"] == "wall_too_thin" for item in dfm["findings"])


def test_profile_notes_are_not_a_print_job(server) -> None:
    notes = tool_payload(server, "fab_x1c_profile_notes", {"material": "PETG"})
    assert notes["ok"] is True
    assert notes["profile_applied"] is False
    assert notes["sliced"] is False
    assert notes["printer_dispatched"] is False
    assert "245" in notes["starting_notes"]["nozzle_c"]
    missing = tool_payload(server, "fab_x1c_profile_notes", {"material": "ABS"})
    assert missing["ok"] is False
    assert "PA-CF" in missing["supported"]


def test_openscad_path_is_used_when_present(server, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(_binary: str, _scad: Path, stl_path: Path) -> tuple[bool, str]:
        write_binary_stl(box(1, 1, 1), stl_path, name="mock")
        return True, "openscad"

    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: "/usr/bin/openscad")
    monkeypatch.setattr("techhand_print_fab.exporting.run_openscad", fake_run)
    created = tool_payload(server, "fab_create_project", {"name": "Mock"})
    tool_payload(
        server,
        "fab_param_model",
        {
            "project_id": created["project_id"],
            "part_name": "cube",
            "backend": "openscad",
            "params": {"kind": "box", "length_mm": 10, "width_mm": 10, "height_mm": 10},
        },
    )
    stl = tool_payload(
        server,
        "fab_export_stl",
        {"project_id": created["project_id"], "part_name": "cube"},
    )
    assert stl["geometry"] == "openscad"
    assert stl["triangle_count"] == 12
    assert stl["printer_dispatched"] is False


def test_source_path_escape_and_include_are_rejected(server, tmp_path: Path) -> None:
    created = tool_payload(server, "fab_create_project", {"name": "Imports"})
    escaped = tool_payload(
        server,
        "fab_param_model",
        {
            "project_id": created["project_id"],
            "part_name": "nope",
            "params": {"kind": "custom_scad"},
            "source_path": "/etc/passwd",
        },
    )
    assert escaped["ok"] is False
    assert "passwd" not in json.dumps(escaped).split("source_path")[-1]
    assert "outside" in escaped["message"]

    included = tool_payload(
        server,
        "fab_param_model",
        {
            "project_id": created["project_id"],
            "part_name": "inline",
            "params": {"kind": "custom_scad", "scad_body": 'include <"/etc/passwd"> cube(1);'},
        },
    )
    assert included["ok"] is False
    assert "include" in included["message"].lower()

    root = tmp_path / "cad"
    root.mkdir()
    (root / "left.scad").write_text("cube([2, 2, 2]);\n", encoding="utf-8")
    (root / "right.scad").write_text("cube([3, 3, 3]);\n", encoding="utf-8")
    monkeypatch_roots = pytest.MonkeyPatch()
    monkeypatch_roots.setenv("FAB_IMPORT_ROOTS", str(root))
    try:
        imported = tool_payload(
            server,
            "fab_param_model",
            {
                "project_id": created["project_id"],
                "part_name": "grip",
                "params": {"material": "ASA"},
                "source_path": str(root),
            },
        )
    finally:
        monkeypatch_roots.undo()
    assert imported["ok"] is True
    slugs = {part["slug"] for part in imported["parts"]}
    assert slugs == {"grip-left", "grip-right"}
    body = Path(imported["parts"][0]["files"]["openscad"]).read_text()
    assert "cube(" in body


def test_core_sources_do_not_call_user_tnt() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "techhand_print_fab"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "techhand_print_fab_tnt" not in text
        assert "user-tnt" not in text


def zip_has_model(path: Path) -> bool:
    with zipfile.ZipFile(path) as package:
        return "3D/3dmodel.model" in package.namelist()
