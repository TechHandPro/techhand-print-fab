"""Upload a sliced .gcode.3mf and start it with print.project_file."""

from __future__ import annotations

import secrets
from typing import Any

from techhand_print_fab.bambu_config import BambuConfig, BambuError, UploadedNotAccepted
from techhand_print_fab.bambu_frames import build_project_file, safe_remote_name
from techhand_print_fab.bambu_ftps import upload_gcode_3mf
from techhand_print_fab.bambu_mqtt import mqtt_request

_LAN_USER = "bblp"


def send_lan(
    config: BambuConfig,
    filename: str,
    data: bytes,
    *,
    plate: int,
    task_name: str,
    bed_type: str,
    bed_leveling: bool,
    flow_cali: bool,
    vibration_cali: bool,
    timelapse: bool,
    use_ams: bool,
    ams_mapping: list[int],
) -> dict[str, Any]:
    remote_name = safe_remote_name(filename)
    url = upload_gcode_3mf(
        config.lan_host,
        config.ftp_port,
        config.access_code,
        remote_name,
        data,
        config.ftp_timeout_s,
        allow_nonprivate=config.allow_nonprivate,
    )
    payload = build_project_file(
        sequence_id=str(secrets.randbelow(1_000_000_000) + 1),
        plate=plate,
        url=url,
        task_name=task_name,
        bed_type=bed_type,
        bed_leveling=bed_leveling,
        flow_cali=flow_cali,
        vibration_cali=vibration_cali,
        timelapse=timelapse,
        use_ams=use_ams,
        ams_mapping=ams_mapping,
    )
    try:
        ack = mqtt_request(
            config.lan_host,
            config.mqtt_port,
            _LAN_USER,
            config.access_code,
            config.serial,
            payload,
            config.mqtt_timeout_s,
            allow_nonprivate=config.allow_nonprivate,
        )
    except BambuError as exc:
        raise UploadedNotAccepted(str(exc), remote_url=url) from None
    result = str(ack.get("result") or "")
    reason = str(ack.get("reason") or "")
    if result.lower() != "success":
        detail = reason or "the printer rejected the job"
        raise UploadedNotAccepted(
            f"Printer rejected the job ({detail}). The file is at {url}.",
            remote_url=url,
        )
    return {
        "remote_url": url,
        "ack_result": "success",
        "ack_reason": reason,
        "request": payload,
        "transport": "lan",
    }
