"""Implicit FTPS upload to the printer on port 990.

Python's FTP_TLS speaks explicit AUTH TLS on port 21. The printer expects TLS
from the first byte. Data connections reuse the control TLS session, which is
what the printer requires, and a missing close_notify after STOR is ignored.
"""

from __future__ import annotations

import ftplib
import io
import socket
import ssl
from collections.abc import Callable

from techhand_print_fab.bambu_config import BambuError, assert_private_host

FtpFactory = Callable[[], ftplib.FTP]


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    def __init__(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        # The printer negotiates TLS 1.2. TLS 1.3 session tickets break the
        # data-connection reuse the printer requires.
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        super().__init__(context=context)

    def connect(
        self,
        host: str = "",
        port: int = 0,
        timeout: float = -999,
        source_address: tuple[str, int] | None = None,
    ) -> str:
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address is not None:
            self.source_address = source_address
        raw = socket.create_connection((self.host, self.port), self.timeout, source_address=self.source_address)
        self.sock = self.context.wrap_socket(raw, server_hostname=self.host)
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(self, cmd: str, rest: str | None = None) -> tuple[socket.socket, int | None]:
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=getattr(self.sock, "session", None),
            )
        return conn, size

    def storbinary(
        self,
        cmd: str,
        fp: io.BufferedIOBase,
        blocksize: int = 8192,
        callback: Callable[[bytes], object] | None = None,
        rest: str | None = None,
    ) -> str:
        self.voidcmd("TYPE I")
        with self.transfercmd(cmd, rest) as conn:
            while True:
                buf = fp.read(blocksize)
                if not buf:
                    break
                conn.sendall(buf)
                if callback:
                    callback(buf)
            if isinstance(conn, ssl.SSLSocket):
                try:
                    conn.unwrap()
                except (ssl.SSLError, OSError):
                    # The printer often drops the data socket without close_notify.
                    pass
        return self.voidresp()


def upload_gcode_3mf(
    host: str,
    port: int,
    access_code: str,
    filename: str,
    data: bytes,
    timeout_s: float,
    *,
    allow_nonprivate: bool,
    ftp_factory: FtpFactory | None = None,
) -> str:
    """Upload into /model when that directory exists. Return the ftp:// URL."""
    assert_private_host(host, allow_nonprivate=allow_nonprivate)
    if any(char in filename for char in "/\\\r\n"):
        raise BambuError("Remote filename is unsafe.")
    ftp = (ftp_factory or ImplicitFTP_TLS)()
    ftp.debugging = 0
    try:
        ftp.connect(host, port, timeout=timeout_s)
        ftp.login(user="bblp", passwd=access_code)
        ftp.prot_p()
        remote_dir = ""
        try:
            ftp.cwd("model")
            remote_dir = "model"
        except ftplib.error_perm:
            remote_dir = ""
        ftp.storbinary(f"STOR {filename}", io.BytesIO(data))
        try:
            ftp.quit()
        except (ftplib.Error, OSError, ssl.SSLError):
            ftp.close()
    except BambuError:
        raise
    except (ftplib.Error, OSError, ssl.SSLError) as exc:
        raise BambuError(f"FTPS upload failed: {exc}") from None
    if remote_dir:
        return f"ftp:///{remote_dir}/{filename}"
    return f"ftp:///{filename}"
