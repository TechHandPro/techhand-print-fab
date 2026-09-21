"""Read nozzle, bed, and job progress. This module does not upload or start a job."""

from __future__ import annotations

import secrets
from typing import Any

from techhand_print_fab.bambu_config import BambuConfig
from techhand_print_fab.bambu_frames import build_pushall, status_snapshot
from techhand_print_fab.bambu_mqtt import mqtt_status

_LAN_USER = "bblp"


def fetch_lan_status(config: BambuConfig, host: str, serial: str) -> dict[str, Any]:
    payload = build_pushall(str(secrets.randbelow(1_000_000_000) + 1))
    body = mqtt_status(
        host,
        config.mqtt_port,
        _LAN_USER,
        config.access_code,
        serial,
        payload,
        config.mqtt_timeout_s,
        allow_nonprivate=config.allow_nonprivate,
    )
    return status_snapshot(body)
