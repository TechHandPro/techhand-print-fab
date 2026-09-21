"""Headless slice: standing defaults, CLI argv, and Studio fallback."""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from techhand_print_fab.print_files import default_model_file
from techhand_print_fab.slicer import apply_bed_metadata, attempt_slice, build_slice_argv, orca_bed_name
from techhand_print_fab.store import get_store
from tests.bambu_support import write_sliced
from tests.conftest import tool_payload
from tests.test_preset_expand import write_profile_tree
from tests.test_print_queue import ACCESS, _enable

_CLOUD = ("bambulab.com", "api.bambulab", "bblcdn")


def _binary(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    path.chmod(0o755)


def _presets(root: Path, materials: tuple[str, ...] = ("PLA",)) -> None:
    (root / "filament").mkdir(parents=True)
    (root / "machine.json").write_text(
        json.dumps(
            {
                "type": "machine",
                "name": "Bambu Lab X1 Carbon 0.4 nozzle",
                "printer_model": "Bambu Lab X1 Carbon",
                "nozzle_diameter": ["0.4"],
                "machine_start_gcode": "M140 S60\n",
            }
        ),
        encoding="utf-8",
    )
    (root / "process.json").write_text(
        json.dumps({"type": "process", "layer_height": "0.2"}),
        encoding="utf-8",
    )
    for material in materials:
        (root / "filament" / f"{material}.json").write_text(
            json.dumps({"type": "filament", "nozzle_temperature": ["220"]}),
            encoding="utf-8",
        )


def _install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, materials: tuple[str, ...] = ("PLA",)) -> None:
    binary = tmp_path / "orca-slicer"
    _binary(binary)
    presets = tmp_path / "presets"
    _presets(presets, materials)
    monkeypatch.setenv("ORCA_SLICER_BIN", str(binary))
    monkeypatch.setenv("FAB_SLICER_PRESETS", str(presets))


def _patch_run(monkeypatch: pytest.MonkeyPatch, *, code: int = 0, stderr: bytes = b"") -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append({"argv": list(argv), "env": kwargs.get("env")})
        if code == 0:
            write_sliced(Path(argv[argv.index("--export-3mf") + 1]))
        return subprocess.CompletedProcess(argv, code, b"", stderr)

    monkeypatch.setattr("techhand_print_fab.slicer.subprocess.run", fake_run)
    return calls


def _plate(server, *, material: str | None = None, part_name: str = "plate") -> str:
    created = tool_payload(server, "fab_create_project", {"name": "Slice plate", "description": "PLA dogfood plate"})
    params: dict[str, object] = {"kind": "plate", "length_mm": 20, "width_mm": 20, "thickness_mm": 3}
    if material is not None:
        params["material"] = material
    tool_payload(
        server,
        "fab_param_model",
        {"project_id": created["project_id"], "part_name": part_name, "params": params},
    )
    return str(created["project_id"])


def test_orca_argv_uses_documented_flags_and_standing_nozzle(tmp_path: Path) -> None:
    machine = tmp_path / "machine.json"
    process = tmp_path / "process.json"
    filament = tmp_path / "PLA.json"
    for path in (machine, process, filament):
        path.write_text("{}", encoding="utf-8")
    argv = build_slice_argv(
        "/usr/bin/orca-slicer",
        tmp_path / "model.stl",
        tmp_path / "model.gcode.3mf",
        machine=machine,
        process=process,
        filament=filament,
        bed_type="textured_plate",
    )
    assert argv[0] == "/usr/bin/orca-slicer"
    assert "--load-settings" in argv
    settings = argv[argv.index("--load-settings") + 1]
    assert settings == f"{process.resolve()};{machine.resolve()}"
    assert argv[argv.index("--load-filaments") + 1] == str(filament.resolve())
    assert "--load-defaultfila" in argv
    assert argv[argv.index("--slice") + 1] == "0"
    assert argv[argv.index("--export-3mf") + 1].endswith("model.gcode.3mf")
    assert argv[argv.index("--nozzle-diameter") + 1] == "0.4"
    assert argv[argv.index("--curr-bed-type") + 1] == "Textured PEI Plate"
    assert orca_bed_name("auto") is None
    assert orca_bed_name("cool_plate") == "Cool Plate"
    blob = " ".join(argv).lower()
    for host in _CLOUD:
        assert host not in blob


def test_newer_mesh_wins_over_stale_gcode(tmp_path: Path) -> None:
    part = tmp_path / "part"
    part.mkdir()
    gcode = part / "model.gcode.3mf"
    stl = part / "model.stl"
    write_sliced(gcode)
    stl.write_bytes(b"stl")
    os.utime(gcode, (1, 1))
    os.utime(stl, (2, 2))
    assert default_model_file(part) == stl
    os.utime(stl, (1, 1))
    os.utime(gcode, (1, 1))
    assert default_model_file(part) == gcode


def test_missing_cli_is_a_studio_handoff(server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("techhand_print_fab.slicer.shutil.which", lambda _name: None)
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    project_id = _plate(server)
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "plate"})
    assert result["ok"] is True
    assert result["mode"] == "studio_handoff"
    assert result["sliced"] is False
    assert result["printer_dispatched"] is False
    assert result["profile_applied"] is False
    assert result["material"] == "PLA"
    assert result["bed_type"] == "textured_plate"
    assert result["nozzle_mm"] == 0.4
    assert "FAB_SLICER_PRESETS" in result["message"]
    assert "inherits" in result["message"]
    assert "orca-slicer" in result["message"]
    handoff = Path(result["handoff_dir"])
    text = (handoff / "HANDOFF.txt").read_text(encoding="utf-8")
    assert "Bambu Studio" in text
    assert "0.4" in text
    assert "textured_plate" in text
    assert (handoff / "model.stl").is_file()
    assert not (get_store().part_dir(project_id, "plate") / "model.gcode.3mf").exists()


def test_inherits_only_preset_is_not_sliced(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    binary = tmp_path / "orca-slicer"
    _binary(binary)
    presets = tmp_path / "presets"
    _presets(presets)
    (presets / "machine.json").write_text(
        json.dumps({"type": "machine", "inherits": "fdm_bbl_3dp_001_common", "nozzle_diameter": ["0.4"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCA_SLICER_BIN", str(binary))
    monkeypatch.setenv("FAB_SLICER_PRESETS", str(presets))
    calls = _patch_run(monkeypatch)
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    project_id = _plate(server)
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "plate"})
    assert result["mode"] == "studio_handoff"
    assert result["sliced"] is False
    assert result["printer_dispatched"] is False
    assert "inherits" in result["message"]
    assert "machine_start_gcode" in result["message"]
    files = {row["file"]: row["status"] for row in result["preset_files"]}
    assert files["machine.json"] == "inherits"
    assert files["process.json"] == "ok"
    assert files["filament/PLA.json"] == "ok"
    assert calls == []


def test_symlink_preset_is_rejected(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    binary = tmp_path / "orca-slicer"
    _binary(binary)
    presets = tmp_path / "presets"
    _presets(presets)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"type": "machine", "machine_start_gcode": "G28\n"}), encoding="utf-8")
    machine = presets / "machine.json"
    machine.unlink()
    machine.symlink_to(outside)
    monkeypatch.setenv("ORCA_SLICER_BIN", str(binary))
    monkeypatch.setenv("FAB_SLICER_PRESETS", str(presets))
    calls = _patch_run(monkeypatch)
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    project_id = _plate(server)
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "plate"})
    assert result["mode"] == "studio_handoff"
    assert "machine.json" in result["message"]
    assert calls == []


def test_fab_slice_writes_gcode_without_printing(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    monkeypatch.setenv("BAMBU_ACCESS_CODE", ACCESS)
    _install(monkeypatch, tmp_path)
    calls = _patch_run(monkeypatch)
    project_id = _plate(server)
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "plate"})
    assert result["ok"] is True
    assert result["mode"] == "sliced"
    assert result["sliced"] is True
    assert result["profile_applied"] is True
    assert result["printer_dispatched"] is False
    assert result["dry_fire"] is True
    assert result["material"] == "PLA"
    assert result["bed_type"] == "textured_plate"
    assert result["slicer"] == "OrcaSlicer"
    assert result["nozzle_mm"] == 0.4
    assert result["sliced_plate"] == "Textured PEI Plate"
    assert result["plate_id"] == "textured_pei"
    process = json.loads((tmp_path / "presets" / "process.json").read_text(encoding="utf-8"))
    assert process["curr_bed_type"] == "Textured PEI Plate"
    gcode = get_store().part_dir(project_id, "plate") / "model.gcode.3mf"
    assert gcode.is_file()
    assert not list(gcode.parent.glob(".model.gcode.3mf.partial"))
    argv = calls[0]["argv"]
    assert isinstance(argv, list)
    assert "--curr-bed-type" in argv
    assert argv[argv.index("--curr-bed-type") + 1] == "Textured PEI Plate"
    assert argv[argv.index("--nozzle-diameter") + 1] == "0.4"
    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert "BAMBU_ACCESS_CODE" not in env
    assert ACCESS not in json.dumps(result)


def test_push_auto_slices_then_stays_a_dry_run(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    sends: list[object] = []
    monkeypatch.setattr("techhand_print_fab.print_job.send_lan", lambda *args, **kwargs: sends.append(kwargs))
    _install(monkeypatch, tmp_path)
    _patch_run(monkeypatch)
    _enable(monkeypatch)
    project_id = _plate(server)
    result = tool_payload(server, "fab_bambu_push_3mf", {"project_id": project_id, "part_name": "plate"})
    assert result["ok"] is True
    assert result["mode"] == "dry_run"
    assert result["sliced"] is True
    assert result["profile_applied"] is True
    assert result["printer_dispatched"] is False
    assert result["material"] == "PLA"
    assert result["bed_type"] == "textured_plate"
    assert result["request"]["print"]["bed_type"] == "textured_plate"
    assert result["request"]["print"]["command"] == "project_file"
    assert "OrcaSlicer wrote" in result["message"]
    assert sends == []
    part = get_store().part_dir(project_id, "plate")
    assert (part / "model.stl").is_file()
    assert (part / "model.gcode.3mf").is_file()


def test_live_push_uses_the_sliced_file(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    seen: dict[str, object] = {}

    def _send(_config: object, filename: str, data: bytes, **kwargs: object) -> dict[str, object]:
        seen["filename"] = filename
        seen["bed_type"] = kwargs["bed_type"]
        seen["data"] = data
        return {
            "remote_url": "ftp:///model/model.gcode.3mf",
            "ack_result": "success",
            "request": {"print": {"command": "project_file"}},
            "transport": "lan",
            "warning": "",
        }

    monkeypatch.setattr("techhand_print_fab.print_job.send_lan", _send)
    _install(monkeypatch, tmp_path)
    _patch_run(monkeypatch)
    _enable(monkeypatch)
    project_id = _plate(server)
    result = tool_payload(
        server,
        "fab_bambu_push_3mf",
        {"project_id": project_id, "part_name": "plate", "confirm": True, "dry_run": False},
    )
    assert result["printer_dispatched"] is True
    assert result["mode"] == "print_dispatch"
    assert result["profile_applied"] is True
    assert result["material"] == "PLA"
    assert seen["filename"] == "model.gcode.3mf"
    assert seen["bed_type"] == "textured_plate"
    assert b"G28" in bytes(seen["data"])  # type: ignore[arg-type]


def test_part_material_is_not_replaced_by_pla(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    _install(monkeypatch, tmp_path, ("PLA",))
    calls = _patch_run(monkeypatch)
    project_id = _plate(server, material="PETG")
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "plate"})
    assert result["material"] == "PETG"
    assert result["mode"] == "studio_handoff"
    assert "filament/PETG.json" in result["message"]
    assert calls == []


def test_explicit_bed_reaches_the_cli(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    _install(monkeypatch, tmp_path)
    calls = _patch_run(monkeypatch)
    project_id = _plate(server)
    result = tool_payload(
        server,
        "fab_slice",
        {"project_id": project_id, "part_name": "plate", "bed_type": "cool_plate"},
    )
    assert result["bed_type"] == "cool_plate"
    assert result["mode"] == "sliced"
    assert result["sliced_plate"] == "Cool Plate"
    assert "plate_id" not in result
    process = json.loads((tmp_path / "presets" / "process.json").read_text(encoding="utf-8"))
    assert process["curr_bed_type"] == "Cool Plate"
    argv = calls[0]["argv"]
    assert isinstance(argv, list)
    assert argv[argv.index("--curr-bed-type") + 1] == "Cool Plate"


def test_auto_bed_omits_the_plate_override(server, monkeypatch: pytest.MonkeyPatch) -> None:
    project_id = _plate(server)
    write_sliced(get_store().part_dir(project_id, "plate") / "model.gcode.3mf")
    _enable(monkeypatch)
    result = tool_payload(
        server,
        "fab_bambu_push_3mf",
        {"project_id": project_id, "part_name": "plate", "bed_type": "auto"},
    )
    assert result["mode"] == "dry_run"
    assert result["bed_type"] == "auto"
    assert result["request"]["print"]["bed_type"] == "auto"
    assert result["profile_applied"] is False


def test_newer_gcode_is_not_resliced(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    _install(monkeypatch, tmp_path)
    calls = _patch_run(monkeypatch)
    project_id = _plate(server)
    tool_payload(server, "fab_export_stl", {"project_id": project_id, "part_name": "plate"})
    part = get_store().part_dir(project_id, "plate")
    write_sliced(part / "model.gcode.3mf")
    result = tool_payload(server, "fab_bambu_push_3mf", {"project_id": project_id, "part_name": "plate"})
    assert result["mode"] == "dry_run"
    assert result["profile_applied"] is False
    assert result["sliced"] is True
    assert calls == []


def test_stale_gcode_is_resliced(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    _install(monkeypatch, tmp_path)
    calls = _patch_run(monkeypatch)
    project_id = _plate(server)
    tool_payload(server, "fab_export_stl", {"project_id": project_id, "part_name": "plate"})
    part = get_store().part_dir(project_id, "plate")
    gcode = part / "model.gcode.3mf"
    write_sliced(gcode)
    os.utime(gcode, (1, 1))
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "plate"})
    assert result["mode"] == "sliced"
    assert result["profile_applied"] is True
    assert calls


def test_failed_cli_is_a_slice_error_without_a_partial(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    monkeypatch.setenv("BAMBU_ACCESS_CODE", ACCESS)
    _install(monkeypatch, tmp_path)
    _patch_run(monkeypatch, code=2, stderr=f"nozzle failed {ACCESS}".encode())
    project_id = _plate(server)
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "plate"})
    assert result["ok"] is False
    assert result["mode"] == "slice_error"
    assert result["printer_dispatched"] is False
    assert result["sliced"] is False
    assert "exited 2" in result["message"]
    assert ACCESS not in json.dumps(result)
    assert "***" in result["message"]
    part = get_store().part_dir(project_id, "plate")
    assert not (part / "model.gcode.3mf").exists()
    assert not list(part.glob(".model.gcode.3mf.partial"))
    assert Path(result["handoff_dir"]).is_dir()


def test_weapon_request_does_not_slice(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, tmp_path)
    calls = _patch_run(monkeypatch)
    project_id = _plate(server, part_name="firearm frame")
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "firearm frame"})
    assert result["refused"] is True
    assert result["policy"] == "weapon_part"
    assert result["printer_dispatched"] is False
    assert calls == []


def test_already_sliced_file_skips_the_cli(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, tmp_path)
    calls = _patch_run(monkeypatch)
    project_id = _plate(server)
    write_sliced(get_store().part_dir(project_id, "plate") / "model.gcode.3mf")
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "plate"})
    assert result["mode"] == "sliced"
    assert result["profile_applied"] is False
    assert result["printer_dispatched"] is False
    assert "already" in result["message"]
    assert calls == []


def test_unspecified_material_on_a_sliced_file_is_pla(server, monkeypatch: pytest.MonkeyPatch) -> None:
    project_id = _plate(server)
    write_sliced(get_store().part_dir(project_id, "plate") / "model.gcode.3mf")
    _enable(monkeypatch)
    result = tool_payload(server, "fab_bambu_push_3mf", {"project_id": project_id, "part_name": "plate"})
    assert result["material"] == "PLA"
    assert result["bed_type"] == "textured_plate"
    assert result["nozzle_mm"] == 0.4
    assert result["printer"] == "Bambu Lab X1 Carbon"
    assert result["request"]["print"]["bed_type"] == "textured_plate"
    assert result["printer_dispatched"] is False


def test_missing_presets_name_each_file(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    binary = tmp_path / "orca-slicer"
    _binary(binary)
    presets = tmp_path / "presets"
    presets.mkdir()
    monkeypatch.setenv("ORCA_SLICER_BIN", str(binary))
    monkeypatch.setenv("FAB_SLICER_PRESETS", str(presets))
    calls = _patch_run(monkeypatch)
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    project_id = _plate(server)
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "plate"})
    assert result["mode"] == "studio_handoff"
    assert result["printer_dispatched"] is False
    files = {row["file"]: row["status"] for row in result["preset_files"]}
    assert files == {
        "machine.json": "missing",
        "process.json": "missing",
        "filament/PLA.json": "missing",
    }
    assert "inherits" in result["message"]
    assert "--expand-presets" in result["message"]
    assert calls == []


def test_inherits_with_start_gcode_is_still_not_sliced(
    server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, tmp_path)
    machine = tmp_path / "presets" / "machine.json"
    machine.write_text(
        json.dumps(
            {
                "type": "machine",
                "inherits": "fdm_bbl_3dp_001_common",
                "machine_start_gcode": "G28\n",
                "nozzle_diameter": ["0.4"],
            }
        ),
        encoding="utf-8",
    )
    calls = _patch_run(monkeypatch)
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    project_id = _plate(server)
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "plate"})
    assert result["mode"] == "studio_handoff"
    assert result["printer_dispatched"] is False
    files = {row["file"]: row["status"] for row in result["preset_files"]}
    assert files["machine.json"] == "inherits"
    assert calls == []


def test_fab_slice_expands_installed_profiles(server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    binary = tmp_path / "orca-slicer"
    _binary(binary)
    write_profile_tree(tmp_path / "profiles")
    monkeypatch.setenv("ORCA_SLICER_BIN", str(binary))
    monkeypatch.delenv("FAB_SLICER_PRESETS", raising=False)
    monkeypatch.setenv("FAB_SLICER_PROFILE_ROOT", str(tmp_path / "profiles"))
    calls = _patch_run(monkeypatch)
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    project_id = _plate(server)
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "plate"})
    assert result["mode"] == "sliced"
    assert result["profile_applied"] is True
    assert result["printer_dispatched"] is False
    assert result["sliced_plate"] == "Textured PEI Plate"
    assert result["plate_id"] == "textured_pei"
    assert result["material"] == "PLA"
    assert result["bed_type"] == "textured_plate"
    cache = Path(os.environ["FAB_DATA_DIR"]) / "slicer-presets" / "x1c-0.4-pla-textured"
    machine = json.loads((cache / "machine.json").read_text(encoding="utf-8"))
    process = json.loads((cache / "process.json").read_text(encoding="utf-8"))
    filament = json.loads((cache / "filament" / "PLA.json").read_text(encoding="utf-8"))
    assert "inherits" not in machine
    assert machine["machine_start_gcode"] == "START\n"
    assert "inherits" not in process
    assert process["curr_bed_type"] == "Textured PEI Plate"
    assert "inherits" not in filament
    assert filament["nozzle_temperature"] == ["220"]
    argv = calls[0]["argv"]
    assert isinstance(argv, list)
    assert str(cache / "process.json") in argv[argv.index("--load-settings") + 1]


def _cool_plate_archive(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = {
        "curr_bed_type": "Cool Plate",
        "cool_plate_temp": ["35"],
        "textured_plate_temp": ["55"],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", b"; curr_bed_type = Cool Plate\nG28\nG1 X1 Y1 E1\n")
        archive.writestr(
            "Metadata/slice_info.config",
            '<config><plate><metadata key="curr_bed_type" value="Cool Plate"/></plate></config>',
        )
        archive.writestr("Metadata/project_settings.config", json.dumps(settings))


def test_cool_plate_metadata_is_rewritten_to_textured(
    server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    _install(monkeypatch, tmp_path)
    process_path = tmp_path / "presets" / "process.json"
    process = json.loads(process_path.read_text(encoding="utf-8"))
    process["curr_bed_type"] = "Cool Plate"
    process_path.write_text(json.dumps(process), encoding="utf-8")

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        _cool_plate_archive(Path(argv[argv.index("--export-3mf") + 1]))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr("techhand_print_fab.slicer.subprocess.run", fake_run)
    project_id = _plate(server)
    result = tool_payload(server, "fab_slice", {"project_id": project_id, "part_name": "plate"})
    assert result["mode"] == "sliced"
    assert result["sliced_plate"] == "Textured PEI Plate"
    assert result["plate_id"] == "textured_pei"
    assert result["bed_type"] == "textured_plate"
    assert result["printer_dispatched"] is False
    written = json.loads(process_path.read_text(encoding="utf-8"))
    assert written["curr_bed_type"] == "Textured PEI Plate"
    gcode = get_store().part_dir(project_id, "plate") / "model.gcode.3mf"
    with zipfile.ZipFile(gcode) as archive:
        header = archive.read("Metadata/plate_1.gcode").decode()
        xml = archive.read("Metadata/slice_info.config").decode()
        settings = json.loads(archive.read("Metadata/project_settings.config"))
    assert header.startswith("; curr_bed_type = Textured PEI Plate\n")
    assert "plate_id = textured_pei" in header
    assert "Cool Plate" not in header
    assert 'value="Textured PEI Plate"' in xml
    assert 'key="plate_id" value="textured_pei"' in xml
    assert "Cool Plate" not in xml
    assert settings["curr_bed_type"] == "Textured PEI Plate"
    assert settings["plate_id"] == "textured_pei"
    assert settings["cool_plate_temp"] == ["35"]
    assert settings["textured_plate_temp"] == ["55"]


def test_auto_bed_does_not_rewrite_cool_plate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "orca-slicer"
    _binary(binary)
    presets = tmp_path / "presets"
    _presets(presets)
    process_path = presets / "process.json"
    process = json.loads(process_path.read_text(encoding="utf-8"))
    process["curr_bed_type"] = "Cool Plate"
    process_path.write_text(json.dumps(process), encoding="utf-8")
    monkeypatch.setenv("ORCA_SLICER_BIN", str(binary))
    monkeypatch.setenv("FAB_SLICER_PRESETS", str(presets))

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        _cool_plate_archive(Path(argv[argv.index("--export-3mf") + 1]))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr("techhand_print_fab.slicer.subprocess.run", fake_run)
    source = tmp_path / "model.stl"
    source.write_bytes(b"stl")
    output = tmp_path / "model.gcode.3mf"
    attempt = attempt_slice(source, output, material="PLA", bed_type="auto")
    assert attempt.ok is True
    assert attempt.mode == "sliced"
    assert attempt.sliced_plate == ""
    assert json.loads(process_path.read_text(encoding="utf-8"))["curr_bed_type"] == "Cool Plate"
    with zipfile.ZipFile(output) as archive:
        assert b"Cool Plate" in archive.read("Metadata/slice_info.config")


def test_apply_bed_metadata_leaves_other_plate_keys(tmp_path: Path) -> None:
    path = tmp_path / "model.gcode.3mf"
    _cool_plate_archive(path)
    apply_bed_metadata(path, "Textured PEI Plate", plate_id="textured_pei")
    with zipfile.ZipFile(path) as archive:
        settings = json.loads(archive.read("Metadata/project_settings.config"))
        gcode = archive.read("Metadata/plate_1.gcode")
        assert gcode.startswith(b"; curr_bed_type = Textured PEI Plate\n")
        assert b"plate_id = textured_pei" in gcode
    assert settings["curr_bed_type"] == "Textured PEI Plate"
    assert settings["plate_id"] == "textured_pei"
    assert settings["cool_plate_temp"] == ["35"]


def test_snake_cool_plate_tag_locks_to_textured_pei(tmp_path: Path) -> None:
    path = tmp_path / "model.gcode.3mf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", b"; curr_bed_type = cool_plate\nG28\n")
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps({"curr_bed_type": "cool_plate", "cool_plate_temp": ["35"]}),
        )
    apply_bed_metadata(path, "Textured PEI Plate", plate_id="textured_pei")
    with zipfile.ZipFile(path) as archive:
        gcode = archive.read("Metadata/plate_1.gcode").decode()
        settings = json.loads(archive.read("Metadata/project_settings.config"))
    assert "cool_plate" not in gcode
    assert "plate_id = textured_pei" in gcode
    assert settings["curr_bed_type"] == "textured_pei"
    assert settings["cool_plate_temp"] == ["35"]
