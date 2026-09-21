"""Discover a printer by the LAN detect probe, optional SSDP, or Farm Manager."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any, Callable

from techhand_print_fab.bambu_config import BambuConfig, BambuError, assert_private_host, load_config
from techhand_print_fab.bambu_farm import FarmClient
from techhand_print_fab.bambu_frames import encode_detect, is_x1c, parse_detect, parse_ssdp
from techhand_print_fab.results import print_result

LAN_PATH_WHY = (
    "LAN Developer Mode is the print path. Bambu documents Developer Mode as the "
    "third-party channel for MQTT on port 8883 and FTP on port 990, which is enough "
    "for one X1 Carbon. Farm Manager is optional: its REST API is a local server on "
    "port 8888, and a printer bound to it closes its own MQTT port. The cloud API is not used."
)

_SSDP = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:2021\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 1\r\n"
    "ST: urn:bambulab-com:device:3dprinter:1\r\n"
    "\r\n"
).encode("ascii")

Probe = Callable[[str], "DetectInfo"]
SsdpListen = Callable[[], list[bytes]]
FarmList = Callable[[BambuConfig], list[dict[str, Any]]]


@dataclass(frozen=True)
class DetectInfo:
    ok: bool
    serial: str = ""
    model: str = ""
    name: str = ""
    detail: str = ""


def probe_printer(host: str, *, port: int = 3000, timeout_s: float = 1.5, allow_nonprivate: bool = False) -> DetectInfo:
    assert_private_host(host, allow_nonprivate=allow_nonprivate)
    frame = encode_detect("1")
    try:
        with socket.create_connection((host, port), timeout_s) as sock:
            sock.settimeout(timeout_s)
            sock.sendall(frame)
            data = _recv_frame(sock)
    except OSError as exc:
        return DetectInfo(ok=False, detail=str(exc))
    try:
        login = parse_detect(data)
    except BambuError as exc:
        return DetectInfo(ok=False, detail=str(exc))
    return DetectInfo(
        ok=True,
        serial=str(login.get("id") or ""),
        model=str(login.get("model") or ""),
        name=str(login.get("name") or ""),
        detail="detect",
    )


def listen_ssdp(timeout_s: float = 1.0) -> list[bytes]:
    packets: list[bytes] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout_s)
        for port in (2021, 1990):
            try:
                sock.sendto(_SSDP, ("239.255.255.250", port))
            except OSError:
                continue
        while True:
            try:
                data, _addr = sock.recvfrom(8192)
            except TimeoutError:
                break
            except OSError:
                break
            packets.append(data)
    finally:
        sock.close()
    return packets


def discover_printers(
    *,
    ssdp: bool = False,
    transport: str = "",
    config: BambuConfig | None = None,
    probe: Probe | None = None,
    ssdp_listen: SsdpListen | None = None,
    farm_list: FarmList | None = None,
) -> dict[str, Any]:
    loaded = config or load_config()
    choice = (transport or "").strip().lower()
    if choice not in {"", "lan", "farm", "both"}:
        raise BambuError("transport must be lan, farm, or both.")
    want_lan = choice in {"", "lan", "both"}
    want_farm = choice in {"", "farm", "both"}
    printers: list[dict[str, Any]] = []
    farm_error = ""
    ssdp_state = "skipped"
    if want_lan and loaded.lan_host:
        assert_private_host(loaded.lan_host, allow_nonprivate=loaded.allow_nonprivate)
        info = (probe or _default_probe(loaded))(loaded.lan_host)
        model = info.model
        name = info.name
        serial = info.serial or loaded.serial
        printers.append(
            {
                "source": "lan",
                "serial": serial,
                "host": loaded.lan_host,
                "model": model,
                "name": name,
                "x1c": is_x1c(model, name),
                "reachable": info.ok,
                "detail": info.detail,
            }
        )
    if want_lan and (ssdp or loaded.discover_ssdp):
        ssdp_state = "ran"
        for packet in (ssdp_listen or listen_ssdp)():
            parsed = parse_ssdp(packet)
            if parsed is None:
                continue
            printers.append(
                {
                    "source": "ssdp",
                    "serial": parsed["serial"],
                    "host": parsed["host"],
                    "model": parsed["model"],
                    "name": parsed["name"],
                    "x1c": is_x1c(parsed["model"], parsed["name"]),
                    "reachable": True,
                    "detail": "ssdp",
                }
            )
    if want_farm and loaded.farm_url:
        try:
            devices = (farm_list or _default_farm)(loaded)
        except BambuError as exc:
            farm_error = str(exc)
            devices = []
        printers.extend(devices)
    printers = _dedupe(printers)
    recommended = ""
    if any(item["source"] in {"lan", "ssdp"} for item in printers):
        recommended = "lan"
    elif any(item["source"] == "farm" for item in printers):
        recommended = "farm"
    message = f"{len(printers)} printer(s)."
    if farm_error and not printers:
        return print_result(
            ok=False,
            message=farm_error,
            printer_dispatched=False,
            mode="discover",
            printers=[],
            recommended_transport="",
            path="lan_developer_mode",
            why=LAN_PATH_WHY,
            ssdp=ssdp_state,
            farm_error=farm_error,
        )
    return print_result(
        ok=True,
        message=message,
        printer_dispatched=False,
        mode="discover",
        printers=printers,
        recommended_transport=recommended,
        path="lan_developer_mode",
        why=LAN_PATH_WHY,
        ssdp=ssdp_state,
        farm_error=farm_error,
    )


def _default_probe(config: BambuConfig) -> Probe:
    def _probe(host: str) -> DetectInfo:
        return probe_printer(host, allow_nonprivate=config.allow_nonprivate)

    return _probe


def _default_farm(config: BambuConfig) -> list[dict[str, Any]]:
    return FarmClient(config).list_devices()


def _dedupe(printers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for printer in printers:
        key = str(printer.get("serial") or "") + "|" + str(printer.get("host") or "") + "|" + str(printer.get("source"))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(printer)
    return ordered


def _recv_frame(sock: socket.socket) -> bytes:
    chunks = bytearray()
    while len(chunks) < 8192:
        try:
            piece = sock.recv(4096)
        except TimeoutError:
            break
        if not piece:
            break
        chunks.extend(piece)
        if chunks.endswith(b"\xa7\xa7"):
            break
    return bytes(chunks)
