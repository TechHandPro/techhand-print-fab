from __future__ import annotations

import json
from pathlib import Path

import pytest

from techhand_print_fab.bambu_config import BambuError, UploadedNotAccepted
from techhand_print_fab.store import get_store
from tests.bambu_support import write_sliced
from tests.conftest import tool_payload

ACCESS = "99887766"


def _plate(server, *, description: str = "PETG calibration plate", part_name: str = "plate") -> str:
    created = tool_payload(
        server,
        "fab_create_project",
        {"name": "Test plate", "description": description},
    )
    tool_payload(
        server,
        "fab_param_model",
        {
            "project_id": created["project_id"],
            "part_name": part_name,
            "params": {
                "kind": "plate",
                "length_mm": 20,
                "width_mm": 20,
                "thickness_mm": 3,
                "material": "PETG",
            },
        },
    )
    return str(created["project_id"])


def _enable(monkeypatch: pytest.MonkeyPatch, *, host: str = "127.0.0.1") -> None:
    monkeypatch.setenv("BAMBU_LAN_HOST", host)
    monkeypatch.setenv("BAMBU_ACCESS_CODE", ACCESS)
    monkeypatch.setenv("BAMBU_SERIAL", "00M09A123456789")
    monkeypatch.setenv("BAMBU_PRINT_ENABLED", "1")


def test_unsliced_stl_is_a_studio_handoff(server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    calls: list[object] = []
    monkeypatch.setattr("techhand_print_fab.print_job.send_lan", lambda *args, **kwargs: calls.append(args))
    project_id = _plate(server)
    tool_payload(server, "fab_export_stl", {"project_id": project_id, "part_name": "plate"})
    result = tool_payload(server, "fab_queue_print", {"project_id": project_id, "part_name": "plate"})
    assert result["ok"] is True
    assert result["mode"] == "studio_handoff"
    assert result["sliced"] is False
    assert result["printer_dispatched"] is False
    assert result["dry_fire"] is True
    assert result["profile_applied"] is False
    assert result["profile_notes"]["material"] == "PETG"
    handoff = Path(result["handoff_dir"])
    assert (handoff / "model.stl").is_file()
    assert "Bambu Studio" in (handoff / "HANDOFF.txt").read_text()
    notes = json.loads((handoff / "x1c-profile-notes.json").read_text())
    assert notes["profile_applied"] is False
    assert calls == []


def test_trainer_part_name_still_hands_off(server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("techhand_print_fab.exporting.openscad_executable", lambda: None)
    project_id = _plate(server, part_name="trainer-trigger-lever")
    tool_payload(server, "fab_export_stl", {"project_id": project_id, "part_name": "trainer-trigger-lever"})
    result = tool_payload(
        server,
        "fab_queue_print",
        {"project_id": project_id, "part_name": "trainer-trigger-lever"},
    )
    assert result["refused"] is False
    assert result["mode"] == "studio_handoff"


def test_weapon_and_disclaimer_are_refused_before_upload(server, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr("techhand_print_fab.print_job.send_lan", lambda *args, **kwargs: calls.append(args))
    project_id = _plate(server, part_name="firearm frame")
    refused = tool_payload(server, "fab_queue_print", {"project_id": project_id, "part_name": "firearm frame"})
    assert refused["refused"] is True
    assert refused["policy"] == "weapon_part"
    assert refused["printer_dispatched"] is False
    described = _plate(server, description="this is not a firearm")
    also = tool_payload(server, "fab_queue_print", {"project_id": described, "part_name": "plate"})
    assert also["refused"] is True
    assert calls == []


def test_sliced_file_plans_until_confirm_and_the_env_flag(server, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def _send(config: object, filename: str, data: bytes, **kwargs: object) -> dict[str, object]:
        calls.append({"filename": filename, "plate": kwargs["plate"]})
        return {
            "remote_url": "ftp:///model/plate.gcode.3mf",
            "ack_result": "success",
            "ack_reason": "success",
            "request": {"print": {"command": "project_file", "md5": ""}},
            "transport": "lan",
            "warning": "",
        }

    monkeypatch.setattr("techhand_print_fab.print_job.send_lan", _send)
    project_id = _plate(server)
    part_dir = get_store().part_dir(project_id, "plate")
    write_sliced(part_dir / "model.gcode.3mf")
    _enable(monkeypatch)
    planned = tool_payload(
        server,
        "fab_queue_print",
        {"project_id": project_id, "part_name": "plate", "confirm": False},
    )
    assert planned["mode"] == "print_plan"
    assert planned["printer_dispatched"] is False
    assert planned["request"]["print"]["command"] == "project_file"
    assert planned["request"]["print"]["md5"] == ""
    assert ACCESS not in json.dumps(planned)
    assert calls == []
    monkeypatch.delenv("BAMBU_PRINT_ENABLED")
    held = tool_payload(
        server,
        "fab_queue_print",
        {"project_id": project_id, "part_name": "plate", "confirm": True},
    )
    assert held["mode"] == "print_plan"
    assert "BAMBU_PRINT_ENABLED" in held["missing"]
    assert calls == []


def test_confirmed_sliced_job_dispatches_on_lan(server, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def _send(config: object, filename: str, data: bytes, **kwargs: object) -> dict[str, object]:
        seen["filename"] = filename
        seen["bytes"] = data
        seen["use_ams"] = kwargs["use_ams"]
        return {
            "remote_url": "ftp:///model/plate.gcode.3mf",
            "ack_result": "success",
            "request": {"print": {"command": "project_file"}},
            "transport": "lan",
            "warning": "",
        }

    monkeypatch.setattr("techhand_print_fab.print_job.send_lan", _send)
    project_id = _plate(server)
    write_sliced(get_store().part_dir(project_id, "plate") / "model.gcode.3mf")
    _enable(monkeypatch)
    result = tool_payload(
        server,
        "fab_queue_print",
        {"project_id": project_id, "part_name": "plate", "confirm": True, "material": "PETG"},
    )
    assert result["ok"] is True
    assert result["mode"] == "print_dispatch"
    assert result["printer_dispatched"] is True
    assert result["dry_fire"] is False
    assert result["profile_applied"] is False
    assert result["remote_url"] == "ftp:///model/plate.gcode.3mf"
    assert seen["filename"] == "model.gcode.3mf"
    assert b"G28" in bytes(seen["bytes"])  # type: ignore[arg-type]
    assert ACCESS not in json.dumps(result)


def test_rejection_and_secrets_stay_out_of_the_result(server, monkeypatch: pytest.MonkeyPatch) -> None:
    def _send(config: object, filename: str, data: bytes, **kwargs: object) -> dict[str, object]:
        raise UploadedNotAccepted(f"no ack {config.access_code}", remote_url="ftp:///model/plate.gcode.3mf")

    monkeypatch.setattr("techhand_print_fab.print_job.send_lan", _send)
    project_id = _plate(server)
    write_sliced(get_store().part_dir(project_id, "plate") / "model.gcode.3mf")
    _enable(monkeypatch)
    result = tool_payload(
        server,
        "fab_queue_print",
        {"project_id": project_id, "part_name": "plate", "confirm": True},
    )
    assert result["ok"] is False
    assert result["printer_dispatched"] is False
    assert result["remote_url"] == "ftp:///model/plate.gcode.3mf"
    assert ACCESS not in json.dumps(result)
    assert "***" in result["message"]


def test_public_host_and_missing_plate_do_not_dispatch(server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "techhand_print_fab.print_job.send_lan",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sent")),
    )
    project_id = _plate(server)
    part_dir = get_store().part_dir(project_id, "plate")
    write_sliced(part_dir / "model.gcode.3mf", plates={2: b"G28\n"})
    _enable(monkeypatch)
    missing_plate = tool_payload(
        server,
        "fab_queue_print",
        {"project_id": project_id, "part_name": "plate", "confirm": True, "plate": 1},
    )
    assert missing_plate["ok"] is False
    assert "Available plates" in missing_plate["message"]
    write_sliced(part_dir / "model.gcode.3mf")
    _enable(monkeypatch, host="8.8.8.8")
    public = tool_payload(
        server,
        "fab_queue_print",
        {"project_id": project_id, "part_name": "plate", "confirm": True},
    )
    assert public["ok"] is False
    assert "private" in public["message"]


def test_lan_queue_only_does_not_upload_and_farm_is_explicit(server, monkeypatch: pytest.MonkeyPatch) -> None:
    lan_calls: list[object] = []
    farm_calls: list[object] = []
    monkeypatch.setattr("techhand_print_fab.print_job.send_lan", lambda *args, **kwargs: lan_calls.append(args))
    monkeypatch.setattr(
        "techhand_print_fab.print_job.send_farm",
        lambda *args, **kwargs: farm_calls.append(kwargs) or {
            "task_id": "9",
            "f3mf_id": "3",
            "device_id": "00M09A123456789",
            "request": {"task_print_model": 1},
            "transport": "farm",
            "remote_url": "",
            "warning": "",
            "queue_only": False,
        },
    )
    project_id = _plate(server)
    write_sliced(get_store().part_dir(project_id, "plate") / "model.gcode.3mf")
    _enable(monkeypatch)
    monkeypatch.setenv("BAMBU_FARM_URL", "https://192.168.1.10:8888")
    monkeypatch.setenv("BAMBU_FARM_TOKEN", "farm-token-value")
    held = tool_payload(
        server,
        "fab_queue_print",
        {"project_id": project_id, "part_name": "plate", "confirm": True, "queue_only": True},
    )
    assert held["mode"] == "print_plan"
    assert lan_calls == []
    farm = tool_payload(
        server,
        "fab_queue_print",
        {
            "project_id": project_id,
            "part_name": "plate",
            "confirm": True,
            "transport": "farm",
            "device_id": "00M09A123456789",
            "use_ams": False,
            "ams_slot": 2,
        },
    )
    assert farm["printer_dispatched"] is True
    assert farm["transport"] == "farm"
    assert farm["task_id"] == "9"
    assert lan_calls == []
    assert farm_calls[0]["ams_slot"] is None


def test_discover_tool_with_no_printer_configured(server) -> None:
    result = tool_payload(server, "fab_discover_printers", {})
    assert result["ok"] is True
    assert result["printers"] == []
    assert result["mode"] == "discover"
    assert result["printer_dispatched"] is False
    assert "Developer Mode" in result["why"]


def test_bad_material_is_rejected(server) -> None:
    project_id = _plate(server)
    result = tool_payload(
        server,
        "fab_queue_print",
        {"project_id": project_id, "part_name": "plate", "material": "wood"},
    )
    assert result["ok"] is False
    assert "PETG" in result["message"]


def test_unconfigured_sliced_file_is_a_plan(server) -> None:
    project_id = _plate(server)
    write_sliced(get_store().part_dir(project_id, "plate") / "model.gcode.3mf")
    result = tool_payload(server, "fab_queue_print", {"project_id": project_id, "part_name": "plate"})
    assert result["mode"] == "print_plan"
    assert result["printer_dispatched"] is False
    assert "BAMBU_LAN_HOST" in result["message"]
