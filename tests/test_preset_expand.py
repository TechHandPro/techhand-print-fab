"""Flatten installed slicer profiles without committing vendor presets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from techhand_print_fab.cli import main
from techhand_print_fab.preset_expand import ensure_presets, inspect_presets


def write_profile_tree(root: Path) -> Path:
    """Synthetic parent/child profiles. Not a Bambu or Orca vendor dump."""
    machine = root / "machine"
    process = root / "process"
    filament = root / "filament"
    for directory in (machine, process, filament):
        directory.mkdir(parents=True)
    (machine / "fdm_common.json").write_text(
        json.dumps(
            {
                "type": "machine",
                "name": "fdm_common",
                "machine_start_gcode": "START\n",
                "printable_area": ["0x0", "256x256"],
            }
        ),
        encoding="utf-8",
    )
    (machine / "Printer 0.4.json").write_text(
        json.dumps(
            {
                "type": "machine",
                "name": "Bambu Lab X1 Carbon 0.4 nozzle",
                "inherits": "fdm_common",
                "nozzle_diameter": ["0.4"],
                "instantiation": "true",
            }
        ),
        encoding="utf-8",
    )
    (process / "base.json").write_text(
        json.dumps(
            {
                "type": "process",
                "name": "fdm_process_common",
                "layer_height": "0.2",
                "curr_bed_type": "Cool Plate",
            }
        ),
        encoding="utf-8",
    )
    (process / "0.20mm Standard @BBL X1C.json").write_text(
        json.dumps(
            {
                "type": "process",
                "name": "0.20mm Standard @BBL X1C",
                "inherits": "fdm_process_common",
                "instantiation": "true",
            }
        ),
        encoding="utf-8",
    )
    (filament / "pla_base.json").write_text(
        json.dumps(
            {
                "type": "filament",
                "name": "fdm_filament_pla",
                "nozzle_temperature": ["210"],
            }
        ),
        encoding="utf-8",
    )
    (filament / "Bambu PLA Basic @BBL X1C.json").write_text(
        json.dumps(
            {
                "type": "filament",
                "name": "Bambu PLA Basic @BBL X1C",
                "inherits": "fdm_filament_pla",
                "instantiation": "true",
                "nozzle_temperature": ["220"],
            }
        ),
        encoding="utf-8",
    )
    return root


def test_expand_drops_inherits_and_sets_textured_plate(tmp_path: Path) -> None:
    tree = write_profile_tree(tmp_path / "profiles")
    dest = tmp_path / "out"
    reports = ensure_presets(dest, tree, "PLA", "Textured PEI Plate", force=True)
    assert [item.status for item in reports] == ["ok", "ok", "ok"]
    machine = json.loads((dest / "machine.json").read_text(encoding="utf-8"))
    process = json.loads((dest / "process.json").read_text(encoding="utf-8"))
    filament = json.loads((dest / "filament" / "PLA.json").read_text(encoding="utf-8"))
    assert "inherits" not in machine
    assert machine["machine_start_gcode"] == "START\n"
    assert machine["printable_area"] == ["0x0", "256x256"]
    assert machine["nozzle_diameter"] == ["0.4"]
    assert "inherits" not in process
    assert process["layer_height"] == "0.2"
    assert process["curr_bed_type"] == "Textured PEI Plate"
    assert "inherits" not in filament
    assert filament["nozzle_temperature"] == ["220"]
    assert (dest / "README.txt").is_file()


def test_expand_keeps_a_full_custom_machine(tmp_path: Path) -> None:
    tree = write_profile_tree(tmp_path / "profiles")
    dest = tmp_path / "out"
    (dest / "filament").mkdir(parents=True)
    (dest / "machine.json").write_text(
        json.dumps({"type": "machine", "machine_start_gcode": "CUSTOM\n", "nozzle_diameter": ["0.4"]}),
        encoding="utf-8",
    )
    ensure_presets(dest, tree, "PLA", "Textured PEI Plate", force=False)
    machine = json.loads((dest / "machine.json").read_text(encoding="utf-8"))
    assert machine["machine_start_gcode"] == "CUSTOM\n"
    process = json.loads((dest / "process.json").read_text(encoding="utf-8"))
    assert process["curr_bed_type"] == "Textured PEI Plate"


def test_inherits_with_required_keys_is_not_ready(tmp_path: Path) -> None:
    (tmp_path / "machine.json").write_text(
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
    (tmp_path / "process.json").write_text(
        json.dumps({"type": "process", "inherits": "fdm_process_common", "layer_height": "0.2"}),
        encoding="utf-8",
    )
    reports = {item.file: item.status for item in inspect_presets(tmp_path, "PLA")}
    assert reports["machine.json"] == "inherits"
    assert reports["process.json"] == "inherits"
    assert reports["filament/PLA.json"] == "missing"


def test_expand_cli_writes_the_three_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = write_profile_tree(tmp_path / "profiles")
    dest = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--expand-presets",
                "--profile-root",
                str(tree),
                "--out",
                str(dest),
                "--material",
                "PLA",
                "--bed",
                "textured_plate",
            ]
        )
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "Textured PEI Plate" in captured.out
    assert (dest / "machine.json").is_file()
    assert (dest / "process.json").is_file()
    assert (dest / "filament" / "PLA.json").is_file()
    process = json.loads((dest / "process.json").read_text(encoding="utf-8"))
    assert process["curr_bed_type"] == "Textured PEI Plate"
    assert "inherits" not in process


def test_expand_cli_names_missing_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit) as exc:
        main(["--expand-presets", "--profile-root", str(empty), "--out", str(tmp_path / "out")])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "machine.json" in err
    assert "process.json" in err
    assert "filament/PLA.json" in err
    assert "resources/profiles" in err


def test_missing_parent_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "profiles"
    (root / "machine").mkdir(parents=True)
    (root / "process").mkdir()
    (root / "filament").mkdir()
    (root / "machine" / "printer.json").write_text(
        json.dumps(
            {
                "type": "machine",
                "name": "Bambu Lab X1 Carbon 0.4 nozzle",
                "inherits": "missing_parent",
                "instantiation": "true",
            }
        ),
        encoding="utf-8",
    )
    (root / "process" / "proc.json").write_text(
        json.dumps({"type": "process", "name": "0.20mm Standard @BBL X1C", "layer_height": "0.2"}),
        encoding="utf-8",
    )
    (root / "filament" / "pla.json").write_text(
        json.dumps(
            {
                "type": "filament",
                "name": "Bambu PLA Basic @BBL X1C",
                "nozzle_temperature": ["220"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        main(["--expand-presets", "--profile-root", str(root), "--out", str(tmp_path / "out")])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "machine.json" in err
    assert "missing_parent" in err
    assert "process.json" in err
    assert "filament/PLA.json" in err
