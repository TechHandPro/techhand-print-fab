"""Optional Bambu Farm Manager client for the local server on port 8888.

LAN Developer Mode is the primary path. This client is for a printer that is
bound to Farm Manager, where the printer's own MQTT port is closed. Routes match
the Farm Manager client captured against server 02.04: login tickets, GET /devices,
POST /file/upload3mf, and POST /task. task_print_model 0 is the captured "queue"
value and 1 is direct print.
"""

from __future__ import annotations

import json
import ssl
import uuid
from collections.abc import Callable
from typing import Any
from urllib import error, request

from techhand_print_fab.bambu_config import BambuConfig, BambuError, farm_origin, scrub
from techhand_print_fab.bambu_frames import is_x1c

_MAX_BODY = 2_000_000
Opener = Callable[[request.Request, float], bytes]


class FarmClient:
    def __init__(self, config: BambuConfig, opener: Opener | None = None) -> None:
        self.config = config
        self.origin = farm_origin(config.farm_url, allow_nonprivate=config.allow_nonprivate)
        self._opener = opener or self._open
        self._token = config.farm_token
        self._server_id = config.farm_server_id
        self._ssl = _ssl_context(config) if config.farm_url.startswith("https://") else None

    def list_devices(self) -> list[dict[str, Any]]:
        self._ensure_auth()
        body = self._json("GET", "/devices")
        devices = body.get("devices") if isinstance(body, dict) else None
        if not isinstance(devices, list):
            raise BambuError("Farm Manager device list was not a list.")
        found: list[dict[str, Any]] = []
        for item in devices:
            if not isinstance(item, dict):
                continue
            serial = str(item.get("dev_id") or "")
            model = str(item.get("dev_model") or "")
            name = str(item.get("name") or item.get("dev_name") or "")
            report = item.get("report_status") if isinstance(item.get("report_status"), dict) else {}
            found.append(
                {
                    "source": "farm",
                    "serial": serial,
                    "host": str(item.get("dev_ip") or ""),
                    "model": model,
                    "name": name,
                    "gcode_state": str(report.get("gcode_state") or ""),
                    "x1c": is_x1c(model, name),
                    "reachable": True,
                }
            )
        return found

    def create_print(
        self,
        device_id: str,
        filename: str,
        data: bytes,
        *,
        task_name: str,
        queue_only: bool,
        bed_leveling: bool,
        flow_cali: bool,
        timelapse: bool,
        ams_mapping: list[dict[str, int]],
    ) -> dict[str, Any]:
        self._ensure_auth()
        uploaded = self._upload(filename, data)
        info = uploaded.get("f3mf_info") if isinstance(uploaded, dict) else None
        if not isinstance(info, dict) or info.get("id") is None:
            raise BambuError("Farm Manager upload did not return f3mf_info.")
        f3mf_id = str(info["id"])
        task_body = {
            "task_print_model": 0 if queue_only else 1,
            "queue_model_cnt": 0,
            "device_pool": [device_id],
            "device_pool2": [{"dev_id": device_id, "ams_mapping2": ams_mapping}],
            "task_name": task_name,
            "print_option": {
                "auto_bed_leveling": bed_leveling,
                "flow_dynamic_calibration": flow_cali,
                "timelapse": timelapse,
                "bed_leveling_mode": 2,
                "flow_dynamic_cali_mode": 2,
                "nozzle_offset_cali_mode": 2,
            },
            "f3mf_id": f3mf_id,
            "ams_mapping2": ams_mapping,
        }
        try:
            created = self._json("POST", "/task", task_body)
        except BambuError as exc:
            raise BambuError(f"{exc} Uploaded file id {f3mf_id}.") from None
        data_obj = created.get("data") if isinstance(created, dict) else None
        task_id = data_obj.get("task_id") if isinstance(data_obj, dict) else created.get("task_id")
        if task_id is None:
            raise BambuError(f"Farm Manager did not return a task id. Uploaded file id {f3mf_id}.")
        return {
            "task_id": str(task_id),
            "f3mf_id": f3mf_id,
            "queue_only": queue_only,
            "device_id": device_id,
            "request": task_body,
        }

    def _ensure_auth(self) -> None:
        if not self._token:
            if not (self.config.farm_username and self.config.farm_password):
                raise BambuError("Farm Manager needs BAMBU_FARM_TOKEN or BAMBU_FARM_USERNAME and BAMBU_FARM_PASSWORD.")
            ticket_body = self._json(
                "POST",
                "/login/local/tickets",
                {"user_name": self.config.farm_username, "password": self.config.farm_password},
                authenticated=False,
            )
            ticket = ticket_body.get("ticket") if isinstance(ticket_body, dict) else None
            if not isinstance(ticket, str) or not ticket:
                raise BambuError("Farm Manager login did not return a ticket.")
            token_body = self._json("POST", "/login/local/tokens", {"ticket": ticket}, authenticated=False)
            token = ""
            if isinstance(token_body, dict):
                for key in ("token", "access_token", "jwt"):
                    if isinstance(token_body.get(key), str):
                        token = str(token_body[key])
                        break
            if not token:
                raise BambuError("Farm Manager login did not return a token.")
            self._token = token
        if not self._server_id:
            try:
                captain = self._json("GET", "/captain")
            except BambuError:
                captain = {}
            if isinstance(captain, dict) and captain.get("server_id"):
                self._server_id = str(captain["server_id"])

    def _upload(self, filename: str, data: bytes) -> dict[str, Any]:
        boundary = "----techhandfab" + uuid.uuid4().hex
        chunks = [
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8"),
            data,
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"folder_id\"\r\n\r\n1\r\n".encode("utf-8"),
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
        headers = self._headers(authenticated=True)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        headers["x-bbl-progress-id"] = str(uuid.uuid4())
        raw = self._send("POST", "/file/upload3mf", b"".join(chunks), headers, timeout=self.config.ftp_timeout_s)
        return _decode(raw, self._secrets())

    def _json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = self._headers(authenticated=authenticated)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        raw = self._send(method, path, payload, headers, timeout=20)
        return _decode(raw, self._secrets())

    def _send(
        self,
        method: str,
        path: str,
        payload: bytes | None,
        headers: dict[str, str],
        timeout: float,
    ) -> bytes:
        req = request.Request(self.origin + path, data=payload, headers=headers, method=method)
        try:
            return self._opener(req, timeout)
        except BambuError:
            raise
        except error.HTTPError as exc:
            raw = exc.read(_MAX_BODY + 1)
            message = _http_message(exc.code, raw, self._secrets())
            raise BambuError(message) from None
        except error.URLError as exc:
            raise BambuError(scrub(f"Farm Manager unreachable: {exc.reason}", self._secrets())) from None

    def _open(self, req: request.Request, timeout: float) -> bytes:
        handlers: list[request.BaseHandler] = [_NoRedirect()]
        if self._ssl is not None:
            handlers.append(request.HTTPSHandler(context=self._ssl))
        opener = request.build_opener(*handlers)
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(_MAX_BODY + 1)
        if len(raw) > _MAX_BODY:
            raise BambuError("Farm Manager response is too large.")
        return raw

    def _headers(self, *, authenticated: bool) -> dict[str, str]:
        headers = {
            "x-bbl-sec-ver": "1",
            "x-bbl-sec-dev": "1",
            "X-BBL-Client-Name": "BambuFarmManager",
            "X-BBL-Client-Type": "farm-web",
            "Accept": "application/json, text/plain, */*",
        }
        if self._server_id:
            headers["x-bbl-sec-sid"] = self._server_id
        if authenticated and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _secrets(self) -> tuple[str, ...]:
        values = [self.config.farm_password, self.config.farm_token, self._token]
        return tuple(value for value in values if len(value) >= 6)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise BambuError(f"Farm Manager redirected ({code}). This client does not follow redirects.")


def _ssl_context(config: BambuConfig) -> ssl.SSLContext:
    if config.farm_tls_insecure:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    context = ssl.create_default_context(cafile=config.farm_ca_file or None)
    if config.farm_cert_file:
        context.load_cert_chain(config.farm_cert_file, config.farm_key_file or None)
    return context


def _decode(raw: bytes, secrets: tuple[str, ...]) -> dict[str, Any]:
    if len(raw) > _MAX_BODY:
        raise BambuError("Farm Manager response is too large.")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BambuError("Farm Manager response was not JSON.") from exc
    if not isinstance(body, dict):
        raise BambuError("Farm Manager response was not a JSON object.")
    code = body.get("code")
    if code not in (None, 0, "0"):
        message = str(body.get("message") or "request failed")
        raise BambuError(scrub(f"Farm Manager error {code}: {message}", secrets))
    return body


def _http_message(status: int, raw: bytes, secrets: tuple[str, ...]) -> str:
    text = raw[:300].decode("utf-8", errors="replace")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return scrub(f"Farm Manager HTTP {status}: {text}", secrets)
    if isinstance(body, dict):
        return scrub(
            f"Farm Manager HTTP {status} error {body.get('code')}: {body.get('message') or text}",
            secrets,
        )
    return scrub(f"Farm Manager HTTP {status}: {text}", secrets)
