from __future__ import annotations

import io
import json
import socket
import struct
import threading

from techhand_print_fab.bambu_frames import (
    build_project_file,
    encode_detect,
    is_x1c,
    parse_detect,
    parse_ssdp,
    safe_remote_name,
)
from techhand_print_fab.bambu_mqtt import (
    MqttSession,
    SocketStream,
    decode_remaining_length,
    encode_connect,
    encode_publish,
    encode_remaining_length,
    encode_subscribe,
    parse_connect,
    parse_publish,
    read_packet,
)

ACCESS = "99887766"


def test_detect_frame_matches_the_observed_layout() -> None:
    payload = b'{"login":{"command":"detect","sequence_id":"20004"}}'
    assert len(payload) == 52
    frame = encode_detect("20004")
    assert frame == b"\xa5\xa5" + struct.pack("<H", 58) + payload + b"\xa7\xa7"
    reply_body = {
        "login": {
            "command": "detect",
            "sequence_id": "20004",
            "id": "00M09A123456789",
            "model": "BL-P001",
            "name": "X1C",
        }
    }
    raw = json.dumps(reply_body, separators=(",", ":")).encode("ascii")
    reply = b"\xa5\xa5" + struct.pack("<H", 6 + len(raw)) + raw + b"\xa7\xa7"
    parsed = parse_detect(reply)
    assert parsed["model"] == "BL-P001"
    assert is_x1c(parsed["model"], parsed["name"]) is True
    assert is_x1c("C12", "P1S") is False


def test_ssdp_parses_x1c_announcement() -> None:
    packet = (
        "HTTP/1.1 200 OK\r\n"
        "USN: 00M09C123456789\r\n"
        "Location: 192.168.1.42\r\n"
        "DevModel.bambu.com: BL-P001\r\n"
        "DevName.bambu.com: Jeremiah X1C\r\n"
        "\r\n"
    ).encode("ascii")
    parsed = parse_ssdp(packet)
    assert parsed is not None
    assert parsed["serial"] == "00M09C123456789"
    assert parsed["host"] == "192.168.1.42"
    assert is_x1c(parsed["model"], parsed["name"]) is True


def test_project_file_omits_credentials() -> None:
    payload = build_project_file(
        sequence_id="7",
        plate=1,
        url="ftp:///model/plate.gcode.3mf",
        task_name="plate",
        bed_type="auto",
        bed_leveling=True,
        flow_cali=True,
        vibration_cali=True,
        timelapse=False,
        use_ams=False,
        ams_mapping=[],
    )
    encoded = json.dumps(payload)
    assert payload["print"]["command"] == "project_file"
    assert payload["print"]["param"] == "Metadata/plate_1.gcode"
    assert payload["print"]["md5"] == ""
    assert payload["print"]["url"].startswith("ftp:///model/")
    assert ACCESS not in encoded
    assert "bblp" not in encoded
    assert safe_remote_name("../My Plate.gcode.3mf") == "My-Plate.gcode.3mf"


def test_remaining_length_round_trip() -> None:
    encoded = encode_remaining_length(128)
    assert encoded == bytes([0x80, 0x01])
    buffer = io.BytesIO(encoded)

    def read(count: int) -> bytes:
        chunk = buffer.read(count)
        assert len(chunk) == count
        return chunk

    assert decode_remaining_length(read) == 128
    packet = encode_publish("device/serial/request", b"x" * 200)
    stream = io.BytesIO(packet)

    def read_packet_bytes(count: int) -> bytes:
        chunk = stream.read(count)
        assert len(chunk) == count
        return chunk

    packet_type, flags, body = read_packet(read_packet_bytes)
    topic, payload = parse_publish(flags, body)
    assert packet_type == 3
    assert topic == "device/serial/request"
    assert payload == b"x" * 200


def test_mqtt_session_round_trip_hides_nothing_but_returns_the_ack() -> None:
    client_sock, server_sock = socket.socketpair()
    client_sock.settimeout(3)
    server_sock.settimeout(3)
    errors: list[BaseException] = []
    seen: dict[str, str] = {}

    def serve() -> None:
        try:
            stream = SocketStream(server_sock)
            packet_type, _flags, body = read_packet(stream.recv_exact)
            assert packet_type == 1
            connect = parse_connect(body)
            seen["username"] = connect["username"]
            seen["password"] = connect["password"]
            stream.sendall(bytes([0x20, 0x02, 0x00, 0x00]))
            sub_type, _sub_flags, _sub_body = read_packet(stream.recv_exact)
            assert sub_type == 8
            stream.sendall(bytes([0x90, 0x03, 0x00, 0x01, 0x00]))
            pub_type, pub_flags, pub_body = read_packet(stream.recv_exact)
            assert pub_type == 3
            _topic, raw = parse_publish(pub_flags, pub_body)
            message = json.loads(raw)
            sequence = message["print"]["sequence_id"]
            assert message["print"]["command"] == "project_file"
            ack = json.dumps(
                {"print": {"sequence_id": sequence, "result": "success", "reason": "success"}}
            ).encode("utf-8")
            stream.sendall(encode_publish("device/00M09A123456789/report", ack))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        session = MqttSession(SocketStream(client_sock))
        ack = session.request(
            client_id="techhand-fab-test",
            username="bblp",
            password=ACCESS,
            report_topic="device/00M09A123456789/report",
            request_topic="device/00M09A123456789/request",
            payload=build_project_file(
                sequence_id="42",
                plate=1,
                url="ftp:///model/plate.gcode.3mf",
                task_name="plate",
                bed_type="auto",
                bed_leveling=True,
                flow_cali=True,
                vibration_cali=True,
                timelapse=False,
                use_ams=False,
                ams_mapping=[],
            ),
            timeout_s=3,
        )
    finally:
        thread.join(timeout=3)
        client_sock.close()
        server_sock.close()
    assert errors == []
    assert seen == {"username": "bblp", "password": ACCESS}
    assert ack["result"] == "success"
    assert encode_connect("id", "bblp", ACCESS)
    assert encode_subscribe("device/00M09A123456789/report")
