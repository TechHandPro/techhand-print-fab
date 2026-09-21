from __future__ import annotations

import json
import socket
import struct
import threading

import pytest

from techhand_print_fab.bambu_config import BambuError
from techhand_print_fab.bambu_discover import DetectInfo, discover_printers, probe_printer
from tests.bambu_support import make_config


def test_probe_reads_a_local_detect_reply() -> None:
    payload = json.dumps(
        {
            "login": {
                "command": "detect",
                "sequence_id": "1",
                "id": "00M09A123456789",
                "model": "BL-P001",
                "name": "X1C",
            }
        },
        separators=(",", ":"),
    ).encode("ascii")
    frame = b"\xa5\xa5" + struct.pack("<H", 6 + len(payload)) + payload + b"\xa7\xa7"
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.settimeout(3)

    def serve() -> None:
        conn, _addr = listener.accept()
        conn.settimeout(3)
        conn.recv(1024)
        conn.sendall(frame)
        conn.close()

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        info = probe_printer("127.0.0.1", port=port, timeout_s=3)
    finally:
        thread.join(timeout=3)
        listener.close()
    assert info.ok is True
    assert info.serial == "00M09A123456789"
    assert info.model == "BL-P001"


def test_discover_uses_lan_probe_and_skips_ssdp() -> None:
    def explode() -> list[bytes]:
        raise AssertionError("ssdp should stay off")

    result = discover_printers(
        config=make_config(lan_host="192.168.1.50", serial="00M09A123456789"),
        probe=lambda host: DetectInfo(True, "00M09A123456789", "BL-P001", "X1C", "detect"),
        ssdp_listen=explode,
    )
    assert result["ok"] is True
    assert result["printer_dispatched"] is False
    assert result["path"] == "lan_developer_mode"
    assert "8883" in result["why"]
    assert "cloud API is not used" in result["why"]
    assert result["ssdp"] == "skipped"
    assert result["recommended_transport"] == "lan"
    assert result["printers"][0]["x1c"] is True
    assert result["printers"][0]["host"] == "192.168.1.50"


def test_discover_merges_ssdp_and_reports_farm_errors() -> None:
    packet = (
        "HTTP/1.1 200 OK\r\n"
        "USN: 00M09C123456789\r\n"
        "Location: 192.168.1.42\r\n"
        "DevModel.bambu.com: 3DPrinter-X1-Carbon\r\n"
        "DevName.bambu.com: Shop\r\n"
        "\r\n"
    ).encode("ascii")

    def farm_list(_config: object) -> list[dict[str, object]]:
        raise BambuError("farm down")

    result = discover_printers(
        ssdp=True,
        config=make_config(farm_url="https://192.168.1.10:8888"),
        ssdp_listen=lambda: [packet],
        farm_list=farm_list,
    )
    assert result["ok"] is True
    assert result["ssdp"] == "ran"
    assert result["farm_error"] == "farm down"
    assert result["printers"][0]["source"] == "ssdp"
    assert result["printers"][0]["x1c"] is True
    assert result["recommended_transport"] == "lan"


def test_public_lan_host_is_rejected_before_a_probe() -> None:
    def explode(_host: str) -> DetectInfo:
        raise AssertionError("probe")

    with pytest.raises(BambuError, match="private"):
        discover_printers(config=make_config(lan_host="8.8.8.8"), probe=explode)
