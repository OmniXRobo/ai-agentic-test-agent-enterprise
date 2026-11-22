"""A small STOMP 1.2 client (pure Python, no extra dependency) for ActiveMQ Classic and Artemis.

Used only for test traffic: SEND to an authorized test destination, and SUBSCRIBE (client-individual
acknowledgement) to an authorized test destination. Business destinations are never consumed from: browsing them goes
through the broker's management API (non-destructive). TLS uses the company CA file when given.
"""

from __future__ import annotations

import socket
import ssl
import threading
import time
from typing import Any

from ..errors import QAError

NUL = b"\x00"
_ESC = {"\\": "\\\\", "\r": "\\r", "\n": "\\n", ":": "\\c"}
_UNESC = {"\\\\": "\\", "\\r": "\r", "\\n": "\n", "\\c": ":"}


def _esc(v: Any) -> str:
    return "".join(_ESC.get(ch, ch) for ch in str(v))


def _unesc(v: str) -> str:
    out, i = [], 0
    while i < len(v):
        if v[i] == "\\" and i + 1 < len(v):
            out.append(_UNESC.get(v[i:i + 2], v[i:i + 2]))
            i += 2
        else:
            out.append(v[i])
            i += 1
    return "".join(out)


class Frame:
    def __init__(self, command: str, headers: dict[str, str] | None = None, body: bytes = b"", *,
                 length: bool = True) -> None:
        self.length = length  # no content-length = a JMS TextMessage on ActiveMQ Classic and Artemis
        self.command = command
        self.headers = headers or {}
        self.body = body

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def encode(self) -> bytes:
        lines = [self.command]
        hdr = dict(self.headers)
        if self.body and self.length:
            hdr["content-length"] = str(len(self.body))
        for k, v in hdr.items():
            if "\n" in str(k) or "\r" in str(k):
                raise QAError("VALIDATION_FAILED", "Invalid message header name.", status=422)
            lines.append(f"{_esc(k)}:{_esc(v)}" if self.command != "CONNECT" else f"{k}:{v}")
        return ("\n".join(lines) + "\n\n").encode("utf-8") + self.body + NUL

    def __repr__(self) -> str:  # never shows passcodes
        h = {k: ("***" if k in ("passcode", "login") else v) for k, v in self.headers.items()}
        return f"Frame({self.command}, {h}, {len(self.body)} bytes)"


class StompClient:
    def __init__(self, host: str, port: int, *, login: str | None = None, passcode: str | None = None,
                 vhost: str | None = None, tls: bool = False, ca_file: str | None = None, verify: bool = True,
                 timeout: float = 10.0, sock_factory: Any = None) -> None:
        self.host, self.port = host, int(port)
        self.login, self.passcode = login, passcode
        self.vhost = vhost or host
        self.tls, self.ca_file, self.verify = tls, ca_file, verify
        self.timeout = timeout
        self.sock_factory = sock_factory  # tests
        self.sock: Any = None
        self.buf = b""
        self.lock = threading.Lock()
        self.server: dict[str, str] = {}
        self._sub = 0
        self._receipt = 0

    # ---- connection ---------------------------------------------------------------------------------------------
    def connect(self, client_id: str | None = None) -> dict[str, str]:
        if any(c in str(v or "") for v in (self.login, self.passcode, self.vhost, client_id) for c in "\r\n\x00"):
            raise QAError("VALIDATION_FAILED", "A user name, password or client id contains a line break.", status=422)
        try:
            raw = self.sock_factory(self.host, self.port) if self.sock_factory else socket.create_connection(
                (self.host, self.port), timeout=self.timeout)
            if self.tls:
                ctx = ssl.create_default_context(cafile=self.ca_file or None)
                if not self.verify:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                raw = ctx.wrap_socket(raw, server_hostname=self.host)
            raw.settimeout(self.timeout)
            self.sock = raw
        except (OSError, ssl.SSLError) as exc:
            raise QAError("AMQ_CONNECT_FAILED", f"Could not open the STOMP connection to {self.host}:{self.port} "
                          f"({type(exc).__name__}).", status=502, details=str(exc)[:200]) from exc
        h = {"accept-version": "1.2,1.1", "host": self.vhost, "heart-beat": "0,0"}
        if self.login:
            h["login"] = self.login
        if self.passcode:
            h["passcode"] = self.passcode
        if client_id:
            h["client-id"] = client_id
        self._send(Frame("CONNECT", h))
        f = self.read(self.timeout)
        if f is None:
            raise QAError("AMQ_CONNECT_FAILED", "The broker did not answer the STOMP CONNECT.", status=502)
        if f.command == "ERROR":
            self.close()
            raise QAError("AMQ_AUTH_FAILED", "The broker refused the STOMP connection: "
                          f"{f.headers.get('message', 'error')[:200]}", status=502)
        if f.command != "CONNECTED":
            raise QAError("AMQ_CONNECT_FAILED", f"Unexpected STOMP answer {f.command}.", status=502)
        self.server = dict(f.headers)
        return self.server

    def close(self) -> None:
        if self.sock is None:
            return
        try:
            self._send(Frame("DISCONNECT", {}))
        except Exception:  # noqa: BLE001
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = None

    def __enter__(self) -> "StompClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- frames ----------------------------------------------------------------------------------------------------
    def _send(self, frame: Frame) -> None:
        if self.sock is None:
            raise QAError("AMQ_NOT_CONNECTED", "Not connected to the broker.", status=409)
        with self.lock:
            self.sock.sendall(frame.encode())

    def read(self, timeout: float) -> Frame | None:
        """The next frame, or None when nothing arrived within ``timeout`` seconds."""
        end = time.monotonic() + timeout
        while True:
            frame = self._parse()
            if frame is not None:
                return frame
            left = end - time.monotonic()
            if left <= 0:
                return None
            self.sock.settimeout(min(left, 1.0))
            try:
                chunk = self.sock.recv(65536)
            except (socket.timeout, TimeoutError):
                continue
            except ssl.SSLWantReadError:
                continue
            if not chunk:
                raise QAError("AMQ_DISCONNECTED", "The broker closed the STOMP connection.", status=502)
            self.buf += chunk
            if len(self.buf) > 32 * 1024 * 1024:
                raise QAError("AMQ_FRAME_TOO_LARGE", "A broker frame is larger than 32 MB.", status=502)

    def _parse(self) -> Frame | None:
        while self.buf.startswith((b"\n", b"\r\n")):  # heart-beats
            self.buf = self.buf[1:] if self.buf.startswith(b"\n") else self.buf[2:]
        head_end = self.buf.find(b"\n\n")
        sep = 2
        crlf = self.buf.find(b"\r\n\r\n")
        if crlf != -1 and (head_end == -1 or crlf < head_end):
            head_end, sep = crlf, 4
        if head_end == -1:
            return None
        head = self.buf[:head_end].decode("utf-8", "replace").replace("\r\n", "\n").split("\n")
        command = head[0].strip()
        headers: dict[str, str] = {}
        for line in head[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                k = _unesc(k) if command != "CONNECTED" else k
                if k not in headers:  # the first value of a repeated header wins (STOMP 1.2)
                    headers[k] = _unesc(v) if command != "CONNECTED" else v
        start = head_end + sep
        if "content-length" in headers:
            try:
                n = int(headers["content-length"])
            except ValueError:
                n = -1
            if n >= 0:
                if len(self.buf) < start + n + 1:
                    return None
                body = self.buf[start:start + n]
                self.buf = self.buf[start + n + 1:]
                return Frame(command, headers, body)
        end = self.buf.find(NUL, start)
        if end == -1:
            return None
        body = self.buf[start:end]
        self.buf = self.buf[end + 1:]
        return Frame(command, headers, body)

    def receipt(self) -> str:
        self._receipt += 1
        return f"r-{self._receipt}"

    def wait_receipt(self, rid: str, timeout: float) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            f = self.read(end - time.monotonic())
            if f is None:
                break
            if f.command == "RECEIPT" and f.headers.get("receipt-id") == rid:
                return
            if f.command == "ERROR":
                raise QAError("AMQ_REFUSED", f"The broker refused: {f.headers.get('message', 'error')[:200]}", status=502,
                              details=f.text()[:300])
        raise QAError("AMQ_NO_RECEIPT", "The broker did not confirm the message (no receipt).", status=502)

    # ---- operations ------------------------------------------------------------------------------------------------
    def send(self, destination: str, body: str | bytes, headers: dict[str, Any] | None = None, *,
             confirm: bool = True) -> None:
        h = {k: str(v) for k, v in (headers or {}).items() if v is not None}
        h["destination"] = destination
        rid = None
        if confirm:
            rid = self.receipt()
            h["receipt"] = rid
        data = body.encode("utf-8") if isinstance(body, str) else body
        text = isinstance(body, str) and NUL not in data
        self._send(Frame("SEND", h, data, length=not text))
        if rid:
            self.wait_receipt(rid, self.timeout)

    def subscribe(self, destination: str, *, selector: str | None = None, ack: str = "client-individual",
                  headers: dict[str, Any] | None = None) -> str:
        self._sub += 1
        sid = f"s-{self._sub}"
        h = {"id": sid, "destination": destination, "ack": ack, **{k: str(v) for k, v in (headers or {}).items()}}
        if selector:
            h["selector"] = selector
        rid = self.receipt()
        h["receipt"] = rid
        self._send(Frame("SUBSCRIBE", h))
        self.wait_receipt(rid, self.timeout)
        return sid

    def unsubscribe(self, sid: str, headers: dict[str, Any] | None = None) -> None:
        self._send(Frame("UNSUBSCRIBE", {"id": sid, **{k: str(v) for k, v in (headers or {}).items()}}))

    def ack(self, frame: Frame) -> None:
        mid = frame.headers.get("ack") or frame.headers.get("message-id")
        if mid:
            self._send(Frame("ACK", {"id": mid}))

    def nack(self, frame: Frame) -> None:
        mid = frame.headers.get("ack") or frame.headers.get("message-id")
        if mid:
            self._send(Frame("NACK", {"id": mid}))

    def receive(self, timeout: float, *, count: int = 1, ack: bool = True) -> list[Frame]:
        out: list[Frame] = []
        end = time.monotonic() + timeout
        while len(out) < count and time.monotonic() < end:
            f = self.read(end - time.monotonic())
            if f is None:
                break
            if f.command == "MESSAGE":
                out.append(f)
                if ack:
                    self.ack(f)
            elif f.command == "ERROR":
                raise QAError("AMQ_REFUSED", f"The broker refused: {f.headers.get('message', 'error')[:200]}",
                              status=502)
        return out


__all__ = ["StompClient", "Frame"]
