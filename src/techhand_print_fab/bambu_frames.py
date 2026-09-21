"""Detect frames, SSDP headers, and the LAN project_file body.

The X1 Carbon prints a sliced .gcode.3mf. Geometry-only STL and 3MF are not a job.
"""

from __future__ import annotations

import json
import re
import struct
from typing import Any
from urllib.parse import urlsplit

from techhand_print_fab.bambu_config import BambuError

_X1C_CODES = frozenset({"bl-p001", "3dprinter-x1-carbon"})
_PLATE_NAME = re.compile(r"Metadata/plate_(\d+)\.gcode$")


def is_x1c(model: str, name: str = "") -> bool:
    blob = f"{model} {name}".lower()
    if any(code in blob for code in _X1C_CODES):
        return True
    return "x1 carbon" in blob or "x1c" in blob


def encode_detect(sequence_id: str = "1") -> bytes:
    payload = json.dumps(
        {"login": {"command": "detect", "sequence_id": sequence_id}},
        separators=(",", ":"),
    ).encode("ascii")
    total = 6 + len(payload)
    return b"\xa5\xa5" + struct.pack("<H", total) + payload + b"\xa7\xa7"


def parse_detect(data: bytes) -> dict[str, Any]:
    if len(data) < 6 or not data.startswith(b"\xa5\xa5"):
        raise BambuError("Detect reply was not a Bambu frame.")
    total = struct.unpack_from("<H", data, 2)[0]
    if total < 6 or total > len(data):
        raise BambuError("Detect reply length does not match the frame.")
    frame = data[:total]
    if not frame.endswith(b"\xa7\xa7"):
        raise BambuError("Detect reply was truncated.")
    try:
        body = json.loads(frame[4:-2].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BambuError("Detect reply was not JSON.") from exc
    login = body.get("login") if isinstance(body, dict) else None
    if not isinstance(login, dict):
        raise BambuError("Detect reply has no login object.")
    return login


def parse_ssdp(packet: bytes) -> dict[str, str] | None:
    text = packet.decode("utf-8", errors="replace")
    headers: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    model = headers.get("devmodel.bambu.com", "")
    name = headers.get("devname.bambu.com", "")
    usn = headers.get("usn", "")
    location = headers.get("location", "")
    if not (model or name or usn):
        return None
    return {
        "model": model,
        "name": name,
        "serial": _serial_from_usn(usn),
        "host": _host_from_location(location),
    }


def plate_param(plate: int) -> str:
    return f"Metadata/plate_{plate}.gcode"


def build_pushall(sequence_id: str) -> dict[str, Any]:
    """Read-only status request. The printer answers on the report topic."""
    return {
        "pushing": {
            "sequence_id": sequence_id,
            "command": "pushall",
            "version": 1,
            "push_target": 1,
        }
    }


def status_snapshot(body: dict[str, Any]) -> dict[str, Any]:
    """Temperatures and job progress from a push_status report. Missing fields stay null."""
    return {
        "nozzle_c": _optional_number(body.get("nozzle_temper")),
        "nozzle_target_c": _optional_number(body.get("nozzle_target_temper")),
        "bed_c": _optional_number(body.get("bed_temper")),
        "bed_target_c": _optional_number(body.get("bed_target_temper")),
        "state": str(body.get("gcode_state") or ""),
        "progress_percent": _optional_number(body.get("mc_percent")),
        "remaining_minutes": _optional_number(body.get("mc_remaining_time")),
        "layer": _optional_number(body.get("layer_num")),
        "total_layers": _optional_number(body.get("total_layer_num")),
        "job_name": str(body.get("subtask_name") or body.get("gcode_file") or ""),
        "ams": summarize_ams(body.get("ams")),
    }


def summarize_ams(raw: object) -> dict[str, Any] | None:
    """None when the payload has no AMS object. An empty unit list is exposed and present false."""
    if not isinstance(raw, dict):
        return None
    trays: list[dict[str, Any]] = []
    units = raw.get("ams")
    if isinstance(units, list):
        for unit in units:
            if not isinstance(unit, dict):
                continue
            unit_id = str(unit.get("id") or "0")
            slots = unit.get("tray")
            if not isinstance(slots, list):
                continue
            for slot in slots:
                if not isinstance(slot, dict):
                    continue
                material = str(slot.get("tray_type") or "")
                slot_id = str(slot.get("id") or "")
                if not material and not slot_id:
                    continue
                trays.append(
                    {
                        "ams_id": unit_id,
                        "slot": slot_id,
                        "type": material,
                        "color": str(slot.get("tray_color") or ""),
                        "remain": _optional_number(slot.get("remain")),
                    }
                )
    bits = str(raw.get("ams_exist_bits") or "")
    return {"present": bits not in {"", "0"} or bool(trays), "trays": trays}


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def plate_pattern() -> re.Pattern[str]:
    return _PLATE_NAME


def build_project_file(
    *,
    sequence_id: str,
    plate: int,
    url: str,
    task_name: str,
    bed_type: str,
    bed_leveling: bool,
    flow_cali: bool,
    vibration_cali: bool,
    timelapse: bool,
    use_ams: bool,
    ams_mapping: list[int],
) -> dict[str, Any]:
    """LAN project_file. md5 stays empty: that is the value observed to be accepted."""
    return {
        "print": {
            "sequence_id": sequence_id,
            "command": "project_file",
            "param": plate_param(plate),
            "project_id": "0",
            "profile_id": "0",
            "task_id": "0",
            "subtask_id": "0",
            "subtask_name": task_name,
            "file": "",
            "url": url,
            "md5": "",
            "timelapse": timelapse,
            "bed_type": bed_type,
            "bed_levelling": bed_leveling,
            "flow_cali": flow_cali,
            "vibration_cali": vibration_cali,
            "layer_inspect": False,
            "use_ams": use_ams,
            "ams_mapping": ams_mapping,
        }
    }


def safe_remote_name(name: str) -> str:
    base = name.replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip(".-")
    if cleaned.lower().endswith(".gcode.3mf"):
        stem = cleaned[: -len(".gcode.3mf")].strip(".-")
    else:
        stem = cleaned
    stem = stem[:80]
    if not re.search(r"[A-Za-z0-9]", stem):
        stem = "fab-job"
    return f"{stem}.gcode.3mf"


def task_label(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9 _.-]+", " ", name).strip()
    text = re.sub(r"\s+", " ", text)[:64].strip()
    return text or "fab-job"


def _serial_from_usn(usn: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]{8,}", usn)
    return tokens[0] if tokens else ""


def _host_from_location(location: str) -> str:
    text = location.strip()
    if not text:
        return ""
    if "://" in text:
        host = urlsplit(text).hostname or ""
        return host
    return text.split(":")[0].strip()
