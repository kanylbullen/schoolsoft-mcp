"""A local https server that records what upload_material sends it.

Used by the tests and by hand against real files. It listens on 127.0.0.1
with a throwaway certificate for ``upload.example.test`` and ``localhost``;
the caller points the guarded resolver at it (see ``route_to_receiver``).
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import ssl
import tempfile
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

RECEIVER_HOST = "upload.example.test"


@dataclass
class Received:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    multipart_field: str | None = None
    multipart_filename: str | None = None
    multipart_content_type: str | None = None
    file_bytes: bytes = b""


@dataclass
class Receiver:
    port: int
    ca_pem: str
    received: list[Received] = field(default_factory=list)

    def url(self, path: str = "/store", host: str = RECEIVER_HOST) -> str:
        return f"https://{host}:{self.port}{path}"

    def ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cadata=self.ca_pem)
        return ctx


def _make_cert(tmp: Path) -> tuple[Path, Path, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, RECEIVER_HOST)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(RECEIVER_HOST),
                    x509.DNSName("localhost"),
                    x509.DNSName("intra.example.test"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_path = tmp / "cert.pem"
    key_path = tmp / "key.pem"
    cert_path.write_bytes(pem)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path, pem.decode()


# A plain Location string answers 307 without keeping the body; a
# (status, Location) pair keeps the body first, like a recipient that stored
# the file and then points elsewhere (303) or redirects badly (302).
Redirect = str | tuple[int, str]


def _handler(receiver: Receiver, redirects: Mapping[str, Redirect]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            target = redirects.get(self.path)
            if isinstance(target, str):
                self._redirect(307, target)
                return
            entry = Received(
                method=self.command,
                path=self.path,
                headers={k.lower(): v for k, v in self.headers.items()},
                body=body,
                file_bytes=body,
            )
            if entry.headers.get("content-type", "").startswith("multipart/form-data"):
                head = f"Content-Type: {entry.headers['content-type']}\r\n\r\n".encode()
                message = BytesParser(policy=HTTP).parsebytes(head + body)
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    entry.multipart_field = name if isinstance(name, str) else None
                    entry.multipart_filename = part.get_filename()
                    entry.multipart_content_type = part.get_content_type()
                    payload = part.get_payload(decode=True)
                    entry.file_bytes = payload if isinstance(payload, bytes) else b""
            receiver.received.append(entry)
            if target is not None:
                self._redirect(*target)
                return
            answer = f'{{"stored": {len(entry.file_bytes)}, "pad": "{"x" * 900}"}}'.encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def _redirect(self, status: int, location: str) -> None:
            self.send_response(status)
            self.send_header("Location", location.replace("{port}", str(receiver.port)))
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_PUT = _handle
        do_POST = _handle

    return Handler


@contextmanager
def run_receiver(redirects: Mapping[str, Redirect] | None = None) -> Iterator[Receiver]:
    """Serve on 127.0.0.1; ``redirects`` maps a path to a redirect (see ``Redirect``).

    ``{port}`` in a Location is replaced with the receiver's own port.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cert, key, pem = _make_cert(Path(tmp))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        receiver = Receiver(port=httpd.server_address[1], ca_pem=pem)
        httpd.RequestHandlerClass = _handler(receiver, redirects or {})
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield receiver
        finally:
            httpd.shutdown()
            httpd.server_close()


FAKE_DNS = {
    RECEIVER_HOST: ["127.0.0.1"],
    "intra.example.test": ["10.0.0.5"],
    "mixed.example.test": ["93.184.216.34", "192.168.1.20"],
}


def route_to_receiver(monkeypatch: Any) -> None:
    """Resolve the test names, and let only the receiver's name reach loopback.

    Everything else keeps the real public-address check, so a redirect to
    ``localhost`` or to a name that resolves privately is still refused.
    """
    from schoolsoft_mcp import delivery

    real_resolve = delivery._resolve
    real_check = delivery._check_addresses

    async def fake_resolve(host: str, port: int) -> list[str]:
        if host in FAKE_DNS:
            return FAKE_DNS[host]
        return await real_resolve(host, port)

    def check(host: str, addresses: list[str]) -> None:
        if host == RECEIVER_HOST:
            return
        real_check(host, addresses)

    monkeypatch.setattr(delivery, "_resolve", fake_resolve)
    monkeypatch.setattr(delivery, "_check_addresses", check)
