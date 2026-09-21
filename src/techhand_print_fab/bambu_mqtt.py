"""Minimal MQTT 3.1.1 over the printer's TLS port 8883.

The printer certificate is a self-signed X.509 v1 whose CN is the serial. It does
not chain to a public CA, so the TLS check is off on this socket only. The access
code is the authenticator, and the host still has to be private unless
BAMBU_ALLOW_NONPRIVATE_HOST=1.
"""

from __future__ import annotations

import json
import socket
import ssl
import struct
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from techhand_print_fab.bambu_config import BambuError, assert_private_host

_CONNACK = 2
_PUBLISH = 3
_SUBSCRIBE = 8
_SUBACK = 9
Reader = Callable[[int], bytes]


class ByteStream(Protocol):
    def sendall(self, data: bytes) -> None: ...

    def recv_exact(self, n: int) -> bytes: ...

    def set_timeout(self, seconds: float) -> None: ...

    def close(self) -> None: ...


class SocketStream:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def sendall(self, data: bytes) -> None:
        self._sock.sendall(data)

    def recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except TimeoutError as exc:
                raise BambuError(_ACK_TIMEOUT) from exc
            if not chunk:
                raise BambuError("MQTT connection closed before an ack.")
            buf.extend(chunk)
        return bytes(buf)

    def set_timeout(self, seconds: float) -> None:
        self._sock.settimeout(seconds)

    def close(self) -> None:
        self._sock.close()


_ACK_TIMEOUT = (
    "No MQTT ack before the timeout. LAN Only mode and Developer Mode both have to be on. "
    "With Developer Mode off the printer accepts the connection and drops control commands."
)
_STATUS_TIMEOUT = (
    "No status report before the timeout. LAN Only mode and Developer Mode both have to be on."
)


def encode_remaining_length(length: int) -> bytes:
    if length < 0 or length > 268_435_455:
        raise BambuError("MQTT remaining length is out of range.")
    out = bytearray()
    while True:
        digit = length % 128
        length //= 128
        if length:
            digit |= 0x80
        out.append(digit)
        if length == 0:
            return bytes(out)


def decode_remaining_length(read: Reader) -> int:
    value = 0
    multiplier = 1
    for _ in range(4):
        digit = read(1)[0]
        value += (digit & 0x7F) * multiplier
        if digit & 0x80 == 0:
            return value
        multiplier *= 128
    raise BambuError("MQTT remaining length is malformed.")


def encode_utf8(text: str) -> bytes:
    raw = text.encode("utf-8")
    if len(raw) > 65535:
        raise BambuError("MQTT string is too long.")
    return struct.pack("!H", len(raw)) + raw


def encode_connect(client_id: str, username: str, password: str, keep_alive: int = 15) -> bytes:
    variable = encode_utf8("MQTT") + bytes([4, 0xC2]) + struct.pack("!H", keep_alive)
    payload = encode_utf8(client_id) + encode_utf8(username) + encode_utf8(password)
    body = variable + payload
    return bytes([0x10]) + encode_remaining_length(len(body)) + body


def encode_publish(topic: str, payload: bytes) -> bytes:
    body = encode_utf8(topic) + payload
    return bytes([0x30]) + encode_remaining_length(len(body)) + body


def encode_subscribe(topic: str, packet_id: int = 1) -> bytes:
    body = struct.pack("!H", packet_id) + encode_utf8(topic) + bytes([0])
    return bytes([0x82]) + encode_remaining_length(len(body)) + body


def read_packet(read: Reader) -> tuple[int, int, bytes]:
    first = read(1)[0]
    remaining = decode_remaining_length(read)
    body = read(remaining) if remaining else b""
    return (first >> 4) & 0x0F, first & 0x0F, body


def parse_publish(flags: int, body: bytes) -> tuple[str, bytes]:
    if len(body) < 2:
        raise BambuError("MQTT PUBLISH is truncated.")
    topic, offset = _take_str(body, 0)
    qos = (flags >> 1) & 0x03
    if qos:
        offset += 2
    if offset > len(body):
        raise BambuError("MQTT PUBLISH is truncated.")
    return topic, body[offset:]


def parse_connect(body: bytes) -> dict[str, str]:
    protocol, offset = _take_str(body, 0)
    if protocol != "MQTT" or offset + 4 > len(body):
        raise BambuError("MQTT CONNECT is truncated.")
    flags = body[offset + 1]
    offset += 4
    client_id, offset = _take_str(body, offset)
    username = ""
    password = ""
    if flags & 0x80:
        username, offset = _take_str(body, offset)
    if flags & 0x40:
        password, offset = _take_str(body, offset)
    return {"client_id": client_id, "username": username, "password": password}


Accept = Callable[[dict[str, Any]], bool]


class MqttSession:
    def __init__(self, stream: ByteStream) -> None:
        self._stream = stream

    def request(
        self,
        *,
        client_id: str,
        username: str,
        password: str,
        report_topic: str,
        request_topic: str,
        payload: dict[str, Any],
        timeout_s: float,
    ) -> dict[str, Any]:
        sequence = str(payload["print"]["sequence_id"])
        return self._exchange(
            client_id=client_id,
            username=username,
            password=password,
            report_topic=report_topic,
            request_topic=request_topic,
            payload=payload,
            timeout_s=timeout_s,
            timeout_message=_ACK_TIMEOUT,
            accept=lambda body: str(body.get("sequence_id")) == sequence and "result" in body,
        )

    def collect(
        self,
        *,
        client_id: str,
        username: str,
        password: str,
        report_topic: str,
        request_topic: str,
        payload: dict[str, Any],
        timeout_s: float,
        accept: Accept,
        timeout_message: str,
    ) -> dict[str, Any]:
        return self._exchange(
            client_id=client_id,
            username=username,
            password=password,
            report_topic=report_topic,
            request_topic=request_topic,
            payload=payload,
            timeout_s=timeout_s,
            timeout_message=timeout_message,
            accept=accept,
        )

    def _exchange(
        self,
        *,
        client_id: str,
        username: str,
        password: str,
        report_topic: str,
        request_topic: str,
        payload: dict[str, Any],
        timeout_s: float,
        timeout_message: str,
        accept: Accept,
    ) -> dict[str, Any]:
        self._stream.set_timeout(timeout_s)
        self._stream.sendall(encode_connect(client_id, username, password))
        packet_type, _flags, body = read_packet(self._stream.recv_exact)
        if packet_type != _CONNACK or len(body) < 2 or body[1] != 0:
            code = body[1] if len(body) > 1 else -1
            raise BambuError(f"MQTT CONNECT was refused (code {code}). Check the access code.")
        self._stream.sendall(encode_subscribe(report_topic))
        packet_type, _flags, _body = read_packet(self._stream.recv_exact)
        if packet_type != _SUBACK:
            raise BambuError("MQTT SUBACK was missing.")
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._stream.sendall(encode_publish(request_topic, raw))
        deadline = _now() + timeout_s
        while True:
            remaining = deadline - _now()
            if remaining <= 0:
                raise BambuError(timeout_message)
            self._stream.set_timeout(remaining)
            packet_type, flags, body = read_packet(self._stream.recv_exact)
            if packet_type != _PUBLISH:
                continue
            _topic, message_raw = parse_publish(flags, body)
            try:
                message = json.loads(message_raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            print_body = message.get("print") if isinstance(message, dict) else None
            if not isinstance(print_body, dict):
                continue
            if accept(print_body):
                return print_body


def _is_status_report(body: dict[str, Any]) -> bool:
    return "gcode_state" in body or "nozzle_temper" in body


def mqtt_status(
    host: str,
    port: int,
    username: str,
    password: str,
    serial: str,
    payload: dict[str, Any],
    timeout_s: float,
    *,
    allow_nonprivate: bool,
) -> dict[str, Any]:
    """Publish pushall and return the first print report that carries state or nozzle temperature."""
    assert_private_host(host, allow_nonprivate=allow_nonprivate)
    try:
        stream = open_tls_stream(host, port, timeout_s)
    except (TimeoutError, OSError) as exc:
        raise BambuError(f"Printer status unreachable: {exc}") from None
    try:
        session = MqttSession(stream)
        try:
            return session.collect(
                client_id=_client_id(),
                username=username,
                password=password,
                report_topic=f"device/{serial}/report",
                request_topic=f"device/{serial}/request",
                payload=payload,
                timeout_s=timeout_s,
                timeout_message=_STATUS_TIMEOUT,
                accept=_is_status_report,
            )
        except (TimeoutError, OSError) as exc:
            raise BambuError(f"Printer status unreachable: {exc}") from None
    finally:
        stream.close()


def mqtt_request(
    host: str,
    port: int,
    username: str,
    password: str,
    serial: str,
    payload: dict[str, Any],
    timeout_s: float,
    *,
    allow_nonprivate: bool,
) -> dict[str, Any]:
    assert_private_host(host, allow_nonprivate=allow_nonprivate)
    stream = open_tls_stream(host, port, timeout_s)
    try:
        session = MqttSession(stream)
        return session.request(
            client_id=_client_id(),
            username=username,
            password=password,
            report_topic=f"device/{serial}/report",
            request_topic=f"device/{serial}/request",
            payload=payload,
            timeout_s=timeout_s,
        )
    finally:
        stream.close()


def open_tls_stream(host: str, port: int, timeout_s: float) -> SocketStream:
    raw = socket.create_connection((host, port), timeout_s)
    raw.settimeout(timeout_s)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    tls = context.wrap_socket(raw, server_hostname=host)
    return SocketStream(tls)


def _take_str(buf: bytes, offset: int) -> tuple[str, int]:
    if offset + 2 > len(buf):
        raise BambuError("MQTT string is truncated.")
    length = struct.unpack_from("!H", buf, offset)[0]
    start = offset + 2
    end = start + length
    if end > len(buf):
        raise BambuError("MQTT string is truncated.")
    return buf[start:end].decode("utf-8"), end


def _client_id() -> str:
    return "techhand-fab-" + uuid.uuid4().hex[:8]


def _now() -> float:
    return time.monotonic()
