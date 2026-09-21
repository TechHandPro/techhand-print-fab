from __future__ import annotations

import zipfile
from pathlib import Path

from techhand_print_fab.bambu_config import BambuConfig


def make_config(**overrides: object) -> BambuConfig:
    values: dict[str, object] = {
        "lan_host": "",
        "access_code": "",
        "serial": "",
        "print_enabled": False,
        "allow_nonprivate": False,
        "discover_ssdp": False,
        "mqtt_port": 8883,
        "ftp_port": 990,
        "mqtt_timeout_s": 2.0,
        "ftp_timeout_s": 5.0,
        "transport": "",
        "farm_url": "",
        "farm_username": "",
        "farm_password": "",
        "farm_token": "",
        "farm_server_id": "",
        "farm_ca_file": "",
        "farm_cert_file": "",
        "farm_key_file": "",
        "farm_tls_insecure": False,
    }
    values.update(overrides)
    return BambuConfig(**values)  # type: ignore[arg-type]


def write_sliced(path: Path, plates: dict[int, bytes] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    chosen = plates or {1: b"G28\nG1 X1 Y1 E1\n"}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
        for plate, gcode in chosen.items():
            archive.writestr(f"Metadata/plate_{plate}.gcode", gcode)
