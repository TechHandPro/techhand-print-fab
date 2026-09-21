from __future__ import annotations

import json
from pathlib import Path

import pytest

from techhand_print_fab.server import create_server
from techhand_print_fab_tnt import register, run_user_tnt
from tests.conftest import tool_names, tool_payload


def _part(server) -> tuple[str, str]:
    created = tool_payload(server, "fab_create_project", {"name": "Handoff"})
    tool_payload(
        server,
        "fab_param_model",
        {
            "project_id": created["project_id"],
            "part_name": "clip",
            "backend": "openscad",
            "params": {"kind": "box", "length_mm": 8, "width_mm": 8, "height_mm": 8},
        },
    )
    return created["project_id"], "clip"


def test_extra_package_entry_point_contract() -> None:
    text = Path(__file__).resolve().parents[1].joinpath("extras", "tnt", "pyproject.toml").read_text()
    assert "techhand_print_fab.bridges" in text
    assert "techhand_print_fab_tnt:register" in text


def test_register_adds_attach_tool(server) -> None:
    assert "fab_attach_to_ticket" not in tool_names(server)
    bridged = create_server()
    register(bridged)
    assert "fab_attach_to_ticket" in tool_names(bridged)


def test_attach_calls_user_tnt_without_claiming_a_print(server, monkeypatch: pytest.MonkeyPatch) -> None:
    project_id, part_name = _part(server)
    bridged = create_server()
    register(bridged)
    captured: dict[str, object] = {}

    def fake_run(payload: dict[str, object]) -> object:
        captured["payload"] = payload

        class Completed:
            returncode = 0
            stdout = b'{"stored": true}'
            stderr = b""

        return Completed()

    monkeypatch.setattr("techhand_print_fab_tnt.run_user_tnt", fake_run)
    result = tool_payload(
        bridged,
        "fab_attach_to_ticket",
        {"ticket_id": 403, "project_id": project_id, "part_name": part_name, "note": "cad v0"},
    )
    assert result["ok"] is True
    assert result["printer_dispatched"] is False
    assert "printer" in result["message"].lower()
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["action"] == "attach_fab_artifact"
    assert payload["ticket_id"] == 403
    assert payload["printer_dispatched"] is False
    names = {item["name"] for item in payload["files"]}
    assert "model.scad" in names
    assert "part.json" in names


def test_missing_user_tnt_is_an_error(server, monkeypatch: pytest.MonkeyPatch) -> None:
    project_id, part_name = _part(server)
    bridged = create_server()
    register(bridged)
    monkeypatch.setenv("USER_TNT_COMMAND", "/no/such/user-tnt-binary")
    result = tool_payload(
        bridged,
        "fab_attach_to_ticket",
        {"ticket_id": 403, "project_id": project_id, "part_name": part_name},
    )
    assert result["ok"] is False
    assert "user-tnt" in result["message"]
    assert result["printer_dispatched"] is False


def test_clone_note_does_not_invoke_user_tnt(server, monkeypatch: pytest.MonkeyPatch) -> None:
    project_id, part_name = _part(server)
    bridged = create_server()
    register(bridged)

    def fake_run(_payload: dict[str, object]) -> object:
        raise AssertionError("user-tnt should not be called")

    monkeypatch.setattr("techhand_print_fab_tnt.run_user_tnt", fake_run)
    result = tool_payload(
        bridged,
        "fab_attach_to_ticket",
        {
            "ticket_id": 403,
            "project_id": project_id,
            "part_name": part_name,
            "note": "exact copy of the commercial product",
        },
    )
    assert result["refused"] is True


def test_user_tnt_argv_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], input: bytes, **_kwargs: object) -> object:
        seen["argv"] = argv
        seen["input"] = input

        class Completed:
            returncode = 0
            stdout = b""
            stderr = b""

        return Completed()

    monkeypatch.setattr("techhand_print_fab_tnt.subprocess.run", fake_run)
    monkeypatch.setenv("USER_TNT_COMMAND", "user-tnt --profile lab")
    completed = run_user_tnt({"action": "attach_fab_artifact", "ticket_id": 1})
    assert completed.returncode == 0
    assert seen["argv"] == ["user-tnt", "--profile", "lab", "attach-fab"]
    assert json.loads(seen["input"])["ticket_id"] == 1  # type: ignore[arg-type]
