from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from techhand_print_fab.bambu_config import BambuError
from techhand_print_fab.print_job import send_farm
from tests.bambu_support import make_config

PASSWORD = "farm-secret-value"
TOKEN = "farm-token-value"


class _FarmServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.hits: list[tuple[str, str, bytes]] = []
        self.fail_login = False
        self.devices: list[dict[str, object]] = [
            {
                "dev_id": "00M09A123456789",
                "dev_model": "BL-P001",
                "dev_ip": "192.168.1.50",
                "name": "X1C",
                "report_status": {"gcode_state": "IDLE"},
            }
        ]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return None

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        host = self.server
        assert isinstance(host, _FarmServer)
        path = urlsplit(self.path).path
        host.hits.append((self.command, path, body))
        self.close_connection = True
        if path == "/login/local/tickets":
            if host.fail_login:
                self._json({"code": 9, "message": PASSWORD})
                return
            self._json({"code": 0, "ticket": "ticket-1"})
            return
        if path == "/login/local/tokens":
            self._json({"code": 0, "token": TOKEN})
            return
        if path == "/captain":
            self._json({"server_id": "server-1", "name": "BambuFarm"})
            return
        if path == "/devices":
            self._json({"devices": host.devices})
            return
        if path == "/file/upload3mf":
            self._json({"code": 0, "f3mf_info": {"id": "503", "f3mf_name": "plate.gcode.3mf"}})
            return
        if path == "/task":
            self._json({"code": 0, "data": {"task_id": 15, "f3mf_id": "503"}})
            return
        self._json({"code": 404, "message": "missing"}, status=404)

    def _json(self, payload: dict[str, object], status: int = 200) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _serve() -> tuple[_FarmServer, threading.Thread]:
    server = _FarmServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server: _FarmServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_farm_login_upload_and_direct_print() -> None:
    server, thread = _serve()
    try:
        config = make_config(
            farm_url=f"http://127.0.0.1:{server.server_address[1]}",
            farm_username="admin",
            farm_password=PASSWORD,
            print_enabled=True,
        )
        result = send_farm(
            config,
            "plate.gcode.3mf",
            b"G28\n",
            device_id="",
            task_name="plate",
            queue_only=False,
            bed_leveling=True,
            flow_cali=True,
            timelapse=False,
            ams_slot=None,
        )
    finally:
        _stop(server, thread)
    assert result["task_id"] == "15"
    assert result["f3mf_id"] == "503"
    assert result["warning"] == ""
    assert PASSWORD not in json.dumps(result)
    assert TOKEN not in json.dumps(result)
    paths = [hit[1] for hit in server.hits]
    assert paths[:2] == ["/login/local/tickets", "/login/local/tokens"]
    assert "/file/upload3mf" in paths
    upload = next(body for method, path, body in server.hits if path == "/file/upload3mf")
    assert b"G28\n" in upload
    task = json.loads(next(body for method, path, body in server.hits if path == "/task"))
    assert task["task_print_model"] == 1
    assert task["f3mf_id"] == "503"
    assert isinstance(task["f3mf_id"], str)
    assert task["device_pool"] == ["00M09A123456789"]


def test_farm_token_skips_login_and_queue_model_is_zero() -> None:
    server, thread = _serve()
    try:
        config = make_config(
            farm_url=f"http://127.0.0.1:{server.server_address[1]}",
            farm_token=TOKEN,
            farm_server_id="server-1",
            print_enabled=True,
        )
        result = send_farm(
            config,
            "plate.gcode.3mf",
            b"G28\n",
            device_id="00M09A123456789",
            task_name="plate",
            queue_only=True,
            bed_leveling=True,
            flow_cali=False,
            timelapse=False,
            ams_slot=2,
        )
    finally:
        _stop(server, thread)
    assert result["queue_only"] is True
    assert "/login/local/tickets" not in [hit[1] for hit in server.hits]
    task = json.loads(next(body for _method, path, body in server.hits if path == "/task"))
    assert task["task_print_model"] == 0
    assert task["ams_mapping2"] == [{"ams_id": 0, "slot_id": 2}]


def test_farm_login_error_scrubs_the_password() -> None:
    server, thread = _serve()
    server.fail_login = True
    try:
        config = make_config(
            farm_url=f"http://127.0.0.1:{server.server_address[1]}",
            farm_username="admin",
            farm_password=PASSWORD,
        )
        with pytest.raises(BambuError) as caught:
            send_farm(
                config,
                "plate.gcode.3mf",
                b"G28\n",
                device_id="",
                task_name="plate",
                queue_only=False,
                bed_leveling=True,
                flow_cali=True,
                timelapse=False,
                ams_slot=None,
            )
    finally:
        _stop(server, thread)
    assert PASSWORD not in str(caught.value)
    assert "***" in str(caught.value)


def test_non_x1c_and_multiple_printers() -> None:
    server, thread = _serve()
    server.devices = [
        {
            "dev_id": "01P00C123456789",
            "dev_model": "C12",
            "dev_ip": "192.168.1.51",
            "name": "P1S",
            "report_status": {"gcode_state": "IDLE"},
        },
        {
            "dev_id": "00M09A123456789",
            "dev_model": "BL-P001",
            "dev_ip": "192.168.1.50",
            "name": "X1C",
            "report_status": {"gcode_state": "IDLE"},
        },
    ]
    config = make_config(
        farm_url=f"http://127.0.0.1:{server.server_address[1]}",
        farm_token=TOKEN,
        farm_server_id="server-1",
    )
    try:
        with pytest.raises(BambuError, match="more than one"):
            send_farm(
                config,
                "plate.gcode.3mf",
                b"G28\n",
                device_id="",
                task_name="plate",
                queue_only=False,
                bed_leveling=True,
                flow_cali=True,
                timelapse=False,
                ams_slot=None,
            )
        warned = send_farm(
            config,
            "plate.gcode.3mf",
            b"G28\n",
            device_id="01P00C123456789",
            task_name="plate",
            queue_only=False,
            bed_leveling=True,
            flow_cali=True,
            timelapse=False,
            ams_slot=None,
        )
    finally:
        _stop(server, thread)
    assert "not marked as an X1 Carbon" in warned["warning"]
