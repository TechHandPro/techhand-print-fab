"""Bambu credentials from the process environment. Nothing here reads a vault or a repo file."""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

_TRUE = frozenset({"1", "true", "yes", "on"})


class BambuError(ValueError):
    """A print or discovery request stopped before a confirmed job."""


class UploadedNotAccepted(BambuError):
    """The file may be on the printer or farm server, and the job was not accepted."""

    def __init__(self, message: str, *, remote_url: str = "", f3mf_id: str = "") -> None:
        super().__init__(message)
        self.remote_url = remote_url
        self.f3mf_id = f3mf_id


@dataclass(frozen=True)
class BambuConfig:
    lan_host: str
    access_code: str
    serial: str
    print_enabled: bool
    allow_nonprivate: bool
    discover_ssdp: bool
    mqtt_port: int
    ftp_port: int
    mqtt_timeout_s: float
    ftp_timeout_s: float
    transport: str
    farm_url: str
    farm_username: str
    farm_password: str
    farm_token: str
    farm_server_id: str
    farm_ca_file: str
    farm_cert_file: str
    farm_key_file: str
    farm_tls_insecure: bool


def load_config() -> BambuConfig:
    return BambuConfig(
        lan_host=os.environ.get("BAMBU_LAN_HOST", "").strip(),
        access_code=os.environ.get("BAMBU_ACCESS_CODE", "").strip(),
        serial=os.environ.get("BAMBU_SERIAL", "").strip(),
        print_enabled=_flag("BAMBU_PRINT_ENABLED"),
        allow_nonprivate=_flag("BAMBU_ALLOW_NONPRIVATE_HOST"),
        discover_ssdp=_flag("BAMBU_DISCOVER_SSDP"),
        mqtt_port=_port("BAMBU_MQTT_PORT", 8883),
        ftp_port=_port("BAMBU_FTP_PORT", 990),
        mqtt_timeout_s=_bounded_float("BAMBU_MQTT_TIMEOUT_S", 8.0, low=1.0, high=60.0),
        ftp_timeout_s=_bounded_float("BAMBU_FTP_TIMEOUT_S", 30.0, low=1.0, high=120.0),
        transport=os.environ.get("BAMBU_TRANSPORT", "").strip().lower(),
        farm_url=os.environ.get("BAMBU_FARM_URL", "").strip(),
        farm_username=os.environ.get("BAMBU_FARM_USERNAME", "").strip(),
        farm_password=os.environ.get("BAMBU_FARM_PASSWORD", "").strip(),
        farm_token=os.environ.get("BAMBU_FARM_TOKEN", "").strip(),
        farm_server_id=os.environ.get("BAMBU_FARM_SERVER_ID", "").strip(),
        farm_ca_file=os.environ.get("BAMBU_FARM_CA_FILE", "").strip(),
        farm_cert_file=os.environ.get("BAMBU_FARM_CERT_FILE", "").strip(),
        farm_key_file=os.environ.get("BAMBU_FARM_KEY_FILE", "").strip(),
        farm_tls_insecure=_flag("BAMBU_FARM_TLS_INSECURE"),
    )


def secret_values(config: BambuConfig) -> tuple[str, ...]:
    values = (config.access_code, config.farm_password, config.farm_token)
    return tuple(value for value in values if len(value) >= 6)


def scrub(text: str, secrets: tuple[str, ...]) -> str:
    cleaned = text
    for secret in secrets:
        cleaned = cleaned.replace(secret, "***")
    return cleaned


def scrub_obj(value: object, secrets: tuple[str, ...]) -> object:
    if isinstance(value, str):
        return scrub(value, secrets)
    if isinstance(value, dict):
        return {str(key): scrub_obj(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_obj(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [scrub_obj(item, secrets) for item in value]
    return value


def assert_private_host(host: str, *, allow_nonprivate: bool) -> None:
    if not host or any(char in host for char in " /@\\"):
        raise BambuError("Printer host must be a hostname or IP with no user info.")
    if len(host) > 253:
        raise BambuError("Printer host is too long.")
    if allow_nonprivate:
        return
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        if host == "localhost" or host.endswith(".local"):
            return
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError as exc:
            raise BambuError(f"Could not resolve the printer host: {exc}") from exc
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
        if not addresses:
            raise BambuError("Printer host did not resolve.")
    for address in addresses:
        if address.is_private or address.is_loopback or address.is_link_local:
            continue
        raise BambuError(
            "Printer host must be a private, loopback, or link-local address. "
            "BAMBU_ALLOW_NONPRIVATE_HOST=1 allows another address."
        )


def farm_origin(url: str, *, allow_nonprivate: bool) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise BambuError("BAMBU_FARM_URL must start with http:// or https://.")
    if parsed.username or parsed.password:
        raise BambuError("Put the Farm Manager password in BAMBU_FARM_PASSWORD.")
    if parsed.hostname is None:
        raise BambuError("BAMBU_FARM_URL has no host.")
    assert_private_host(parsed.hostname, allow_nonprivate=allow_nonprivate)
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    if parsed.port is None:
        return f"{parsed.scheme}://{host}"
    return f"{parsed.scheme}://{host}:{parsed.port}"


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def _port(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdigit():
        raise BambuError(f"{name} must be a TCP port.")
    value = int(raw)
    if value < 1 or value > 65535:
        raise BambuError(f"{name} must be a TCP port.")
    return value


def _bounded_float(name: str, default: float, *, low: float, high: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise BambuError(f"{name} must be a number of seconds.") from exc
    if value < low or value > high:
        raise BambuError(f"{name} must be between {low:g} and {high:g} seconds.")
    return value
