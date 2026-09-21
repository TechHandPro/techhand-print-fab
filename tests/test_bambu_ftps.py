from __future__ import annotations

import socket
import ssl
import subprocess
import threading
from pathlib import Path

import ftplib
import pytest

from techhand_print_fab.bambu_config import BambuError
from techhand_print_fab.bambu_ftps import ImplicitFTP_TLS, upload_gcode_3mf


class _FakeFTP:
    def __init__(self, *, cwd_ok: bool) -> None:
        self.debugging = 0
        self.cwd_ok = cwd_ok
        self.stored = b""
        self.user = ""

    def connect(self, host: str, port: int, timeout: float | None = None) -> str:
        assert host == "127.0.0.1"
        assert port == 990
        return "220 ready"

    def login(self, user: str = "", passwd: str = "", acct: str = "") -> str:
        self.user = user
        assert passwd == "99887766"
        return "230"

    def prot_p(self) -> str:
        return "200"

    def cwd(self, name: str) -> str:
        if not self.cwd_ok:
            raise ftplib.error_perm("550 missing")
        assert name == "model"
        return "250"

    def storbinary(self, cmd: str, fp: object, blocksize: int = 8192, callback: object = None, rest: object = None) -> str:
        assert cmd == "STOR plate.gcode.3mf"
        self.stored = fp.read()  # type: ignore[attr-defined]
        return "226"

    def quit(self) -> str:
        return "221"

    def close(self) -> None:
        return None


def test_upload_prefers_model_directory() -> None:
    ftp = _FakeFTP(cwd_ok=True)
    url = upload_gcode_3mf(
        "127.0.0.1",
        990,
        "99887766",
        "plate.gcode.3mf",
        b"gcode",
        5,
        allow_nonprivate=False,
        ftp_factory=lambda: ftp,
    )
    assert url == "ftp:///model/plate.gcode.3mf"
    assert "99887766" not in url
    assert ftp.user == "bblp"
    assert ftp.stored == b"gcode"


def test_upload_falls_back_to_ftp_root() -> None:
    ftp = _FakeFTP(cwd_ok=False)
    url = upload_gcode_3mf(
        "127.0.0.1",
        990,
        "99887766",
        "plate.gcode.3mf",
        b"gcode",
        5,
        allow_nonprivate=False,
        ftp_factory=lambda: ftp,
    )
    assert url == "ftp:///plate.gcode.3mf"


def test_upload_rejects_a_public_host_and_a_newline_name() -> None:
    with pytest.raises(BambuError):
        upload_gcode_3mf("8.8.8.8", 990, "99887766", "plate.gcode.3mf", b"g", 5, allow_nonprivate=False)
    with pytest.raises(BambuError):
        upload_gcode_3mf("127.0.0.1", 990, "99887766", "bad\nname.gcode.3mf", b"g", 5, allow_nonprivate=False)


def test_data_connection_reuses_the_control_tls_session(monkeypatch: pytest.MonkeyPatch) -> None:
    ftp = ImplicitFTP_TLS()
    ftp._prot_p = True
    ftp.host = "127.0.0.1"

    class _Control:
        session = "session-token"
        family = socket.AF_INET

    ftp.sock = _Control()  # type: ignore[assignment]
    left, right = socket.socketpair()
    monkeypatch.setattr(ftplib.FTP, "ntransfercmd", lambda self, cmd, rest=None: (left, None))
    seen: dict[str, object] = {}

    class _Context:
        def wrap_socket(self, conn: socket.socket, server_hostname: str | None = None, session: object = None) -> socket.socket:
            seen["session"] = session
            seen["host"] = server_hostname
            return conn

    ftp.context = _Context()  # type: ignore[assignment]
    try:
        conn, _size = ftp.ntransfercmd("STOR plate.gcode.3mf")
    finally:
        left.close()
        right.close()
    assert conn is left
    assert seen == {"session": "session-token", "host": "127.0.0.1"}


def test_implicit_ftps_stor_round_trip(tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    received = bytearray()
    ready = threading.Event()
    port_holder: dict[str, int] = {}

    def serve() -> None:
        listener = socket.create_server(("127.0.0.1", 0))
        port_holder["port"] = listener.getsockname()[1]
        listener.settimeout(5)
        ready.set()
        raw, _addr = listener.accept()
        raw.settimeout(5)
        control = context.wrap_socket(raw, server_side=True)

        def send(line: str) -> None:
            control.sendall((line + "\r\n").encode("ascii"))

        def read_line() -> str:
            buf = bytearray()
            while b"\r\n" not in buf:
                chunk = control.recv(1)
                if not chunk:
                    break
                buf.extend(chunk)
            return bytes(buf).decode("ascii", errors="replace").strip()

        send("220 ready")
        data_sock: ssl.SSLSocket | None = None
        raw_data: socket.socket | None = None
        data_listener: socket.socket | None = None
        while True:
            line = read_line()
            if line.upper().startswith("USER"):
                send("331 password")
            elif line.upper().startswith("PASS"):
                send("230 logged in")
            elif line.upper().startswith("PBSZ") or line.upper().startswith("PROT") or line.upper().startswith("TYPE"):
                send("200 ok")
            elif line.upper().startswith("CWD"):
                send("250 ok")
            elif line.upper().startswith("PASV"):
                data_listener = socket.create_server(("127.0.0.1", 0))
                data_port = data_listener.getsockname()[1]
                high, low = divmod(data_port, 256)
                send(f"227 Entering Passive Mode (127,0,0,1,{high},{low})")
                data_listener.settimeout(5)
                incoming, _addr = data_listener.accept()
                incoming.settimeout(5)
                # Accept TCP now, but do not handshake yet. The client wraps
                # the data socket only after it has read the 150 reply.
                raw_data = incoming
            elif line.upper().startswith("STOR"):
                send("150 ok")
                assert raw_data is not None
                data_sock = context.wrap_socket(raw_data, server_side=True)
                raw_data = None
                while True:
                    chunk = data_sock.recv(65536)
                    if not chunk:
                        break
                    received.extend(chunk)
                data_sock.close()
                data_sock = None
                send("226 Transfer complete")
            elif line.upper().startswith("QUIT") or not line:
                send("221 bye")
                break
            else:
                send("502 unsupported")
        control.close()
        if data_sock is not None:
            data_sock.close()
        if raw_data is not None:
            raw_data.close()
        if data_listener is not None:
            data_listener.close()
        listener.close()

    thread = threading.Thread(target=serve)
    thread.start()
    assert ready.wait(5)
    try:
        url = upload_gcode_3mf(
            "127.0.0.1",
            port_holder["port"],
            "99887766",
            "plate.gcode.3mf",
            b"G28\n",
            5,
            allow_nonprivate=False,
        )
    finally:
        thread.join(timeout=5)
    assert url == "ftp:///model/plate.gcode.3mf"
    assert bytes(received) == b"G28\n"
