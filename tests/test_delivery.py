"""upload_material and save_to: the two ways a file leaves without the model."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import socket
from pathlib import Path

import httpcore
import httpx
import pytest
import respx

pytest.importorskip("cryptography")

from schoolsoft_mcp import delivery, server
from schoolsoft_mcp.client import SchoolSoftClient
from schoolsoft_mcp.config import ConfigError, Settings
from schoolsoft_mcp.models import AttachmentBytes, UploadResult
from schoolsoft_mcp.server import AppContext

from .upload_receiver import RECEIVER_HOST, Redirect, route_to_receiver, run_receiver

ALLOWED = ("example.test", "localhost")
PDF = b"%PDF-1.4\n" + bytes(range(256)) * 100

BASE = "https://example.test"
ROOT = f"{BASE}/yourschool"
SECTION = 900202
FILE = 900203


async def _send(url: str, receiver_ctx: object, **kw: object) -> delivery.UploadResponse:
    args: dict[str, object] = {
        "url": url,
        "method": "PUT",
        "content": PDF,
        "filename": "Läxa vecka 47.pdf",
        "content_type": "application/pdf",
        "form_field": None,
        "headers": None,
        "allowed_domains": ALLOWED,
        "timeout_s": 10.0,
        "ssl_context": receiver_ctx,
    }
    args.update(kw)
    return await delivery.upload(**args)  # type: ignore[arg-type]


# --- target checks ---------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("http://upload.example.test/x", "https"),
        ("ftp://upload.example.test/x", "https"),
        ("https://elsewhere.example.org/x?sig=abc", "'elsewhere.example.org' is not in"),
        ("https://notexample.test/x", "'notexample.test' is not in"),
        ("https://user:pw" + "\x40" + "upload.example.test/x", "user name"),
        ("not a url", "https"),
    ],
)
def test_targets_are_refused(url: str, fragment: str) -> None:
    with pytest.raises(delivery.DeliveryRefused) as exc:
        delivery.check_target(url, ALLOWED)
    assert fragment in str(exc.value)
    assert "sig=abc" not in str(exc.value)


def test_an_empty_allowlist_refuses_everything() -> None:
    with pytest.raises(delivery.DeliveryRefused) as exc:
        delivery.check_target("https://upload.example.test/x", ())
    assert "SCHOOLSOFT_UPLOAD_ALLOWED_DOMAINS is empty" in str(exc.value)
    assert "'upload.example.test'" in str(exc.value)


def test_subdomains_of_an_allowed_domain_pass() -> None:
    assert delivery.check_target("https://a.b.example.test/x", ALLOWED).host == "a.b.example.test"


@pytest.mark.parametrize("host", ["127.0.0.1", "10.1.2.3", "192.168.0.10", "[::1]", "169.254.1.1"])
def test_private_ip_literals_are_refused(host: str) -> None:
    allowed = (host.strip("[]"),)
    with pytest.raises(delivery.DeliveryRefused, match="not a public address"):
        delivery.check_target(f"https://{host}/x", allowed)


@pytest.mark.parametrize(
    "addresses",
    [["127.0.0.1"], ["10.0.0.5"], ["93.184.216.34", "192.168.1.20"], ["::ffff:10.0.0.1"], ["fe80::1"]],
)
def test_names_that_resolve_privately_are_refused(addresses: list[str]) -> None:
    with pytest.raises(delivery.DeliveryRefused, match="non-public"):
        delivery._check_addresses("x.example.test", addresses)


def test_public_addresses_pass() -> None:
    delivery._check_addresses("x.example.test", ["93.184.216.34", "2606:2800:220:1::1"])


def test_allowlist_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "SCHOOLSOFT_SCHOOL": "yourschool",
        "SCHOOLSOFT_USERNAME": "alice",
        "SCHOOLSOFT_PASSWORD": "secret",
        "SCHOOLSOFT_UPLOAD_ALLOWED_DOMAINS": "Example.COM, *.uploads.example.org .x.test",
    }.items():
        monkeypatch.setenv(key, value)
    assert Settings.from_env().upload_allowed_domains == (
        "example.com",
        "uploads.example.org",
        "x.test",
    )
    monkeypatch.setenv("SCHOOLSOFT_UPLOAD_ALLOWED_DOMAINS", "https://example.com/")
    with pytest.raises(ConfigError):
        Settings.from_env()
    monkeypatch.delenv("SCHOOLSOFT_UPLOAD_ALLOWED_DOMAINS")
    assert Settings.from_env().upload_allowed_domains == ()


# --- uploads against a real local TLS server --------------------------------


async def test_put_sends_raw_body_with_type_and_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    route_to_receiver(monkeypatch)
    with run_receiver() as rx:
        result = await _send(rx.url(), rx.ssl_context(), headers={"x-upload-key": "k1"})
    assert result.status_code == 201
    assert len(result.excerpt) == delivery.RESPONSE_EXCERPT_CHARS
    (got,) = rx.received
    assert got.method == "PUT"
    assert hashlib.sha256(got.body).digest() == hashlib.sha256(PDF).digest()
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["x-upload-key"] == "k1"
    disposition = got.headers["content-disposition"]
    assert 'filename="Laxa vecka 47.pdf"' in disposition
    assert "filename*=UTF-8''L%C3%A4xa%20vecka%2047.pdf" in disposition


async def test_post_multipart_names_the_field_and_file(monkeypatch: pytest.MonkeyPatch) -> None:
    route_to_receiver(monkeypatch)
    with run_receiver() as rx:
        result = await _send(rx.url(), rx.ssl_context(), method="post", form_field="file")
    assert result.status_code == 201
    (got,) = rx.received
    assert got.method == "POST"
    assert got.multipart_field == "file"
    assert got.multipart_filename == "Läxa vecka 47.pdf"
    assert got.multipart_content_type == "application/pdf"
    assert got.file_bytes == PDF


async def test_a_307_within_the_allowlist_is_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    route_to_receiver(monkeypatch)
    redirects = {"/go": f"https://{RECEIVER_HOST}:{{port}}/store"}
    with run_receiver(redirects) as rx:
        result = await _send(rx.url("/go"), rx.ssl_context(), headers={"x-upload-key": "k1"})
    assert result.status_code == 201
    assert result.redirected
    assert [r.path for r in rx.received] == ["/store"]
    assert rx.received[0].headers["x-upload-key"] == "k1"


async def test_keys_do_not_follow_a_redirect_to_another_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_to_receiver(monkeypatch)
    # Same name, another port: a different origin, so possibly another service.
    with run_receiver() as other, run_receiver({"/go": other.url()}) as first:
        trust_both = first.ssl_context()
        trust_both.load_verify_locations(cadata=other.ca_pem)
        result = await _send(
            first.url("/go"),
            trust_both,
            headers={"x-upload-key": "k1", "Content-Type": "application/x-pdf"},
        )
    assert result.status_code == 201
    assert first.received == []
    (got,) = other.received
    assert "x-upload-key" not in got.headers
    assert got.headers["content-type"] == "application/x-pdf"
    assert got.body == PDF


@pytest.mark.parametrize(("status", "accepted"), [(303, True), (302, False), (301, False)])
async def test_a_redirect_after_storing_is_not_followed(
    monkeypatch: pytest.MonkeyPatch, status: int, accepted: bool
) -> None:
    route_to_receiver(monkeypatch)
    with run_receiver({"/store": (status, "/done")}) as rx:
        result = await _send(rx.url(), rx.ssl_context(), method="POST", form_field="file")
    assert result.status_code == status
    assert result.unfollowed_redirect
    assert result.accepted is accepted
    assert [r.path for r in rx.received] == ["/store"]


@pytest.mark.parametrize(
    ("location", "fragment"),
    [
        ("https://localhost:{port}/store", "non-public"),
        ("https://intra.example.test:{port}/store", "non-public"),
        ("https://mixed.example.test:{port}/store", "non-public"),
        ("https://127.0.0.1:{port}/store", "not in SCHOOLSOFT_UPLOAD_ALLOWED_DOMAINS"),
        ("http://upload.example.test:{port}/store", "https"),
        ("https://evil.example.org/store", "not in SCHOOLSOFT_UPLOAD_ALLOWED_DOMAINS"),
    ],
)
async def test_redirects_to_forbidden_places_are_refused(
    monkeypatch: pytest.MonkeyPatch, location: str, fragment: str
) -> None:
    route_to_receiver(monkeypatch)
    with (
        run_receiver({"/go": location}) as rx,
        pytest.raises(delivery.DeliveryRefused, match=fragment),
    ):
        await _send(rx.url("/go"), rx.ssl_context())
    assert rx.received == []


async def test_a_name_resolving_to_loopback_is_refused_at_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_to_receiver(monkeypatch)
    with run_receiver() as rx, pytest.raises(delivery.DeliveryRefused, match="non-public"):
        await _send(rx.url(host="localhost"), rx.ssl_context())
    assert rx.received == []


async def test_the_url_never_reaches_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    route_to_receiver(monkeypatch)
    caplog.set_level(logging.DEBUG)
    with run_receiver() as rx:
        await _send(rx.url("/store?sig=SECRETSIG"), rx.ssl_context(), headers={"x-k": "SECRETHDR"})
    assert "SECRETSIG" not in caplog.text
    assert "SECRETHDR" not in caplog.text


async def test_a_name_that_does_not_resolve_is_an_upload_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_such_name(host: str, port: int) -> list[str]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(delivery, "_resolve", no_such_name)
    with pytest.raises(delivery.UploadFailed, match="Could not send to"):
        await _send("https://upload.example.test/x?sig=S", None)


async def test_a_slow_name_lookup_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(host: str, port: int) -> list[str]:
        await asyncio.sleep(5)
        return ["93.184.216.34"]

    monkeypatch.setattr(delivery, "_resolve", slow)
    with pytest.raises(delivery.UploadFailed, match="Timed out"):
        await _send("https://upload.example.test/x", None, timeout_s=0.2)


async def test_a_connect_timeout_tries_the_next_address(monkeypatch: pytest.MonkeyPatch) -> None:
    async def two(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "93.184.216.35"]

    tried: list[str] = []
    sentinel = object()

    class Inner:
        async def connect_tcp(self, address: str, port: int, **kw: object) -> object:
            tried.append(address)
            if address.endswith(".34"):
                raise httpcore.ConnectTimeout("slow")
            return sentinel

    monkeypatch.setattr(delivery, "_resolve", two)
    backend = delivery._PublicOnlyBackend()
    backend._inner = Inner()  # type: ignore[assignment]
    assert await backend.connect_tcp("upload.example.test", 443, timeout=1.0) is sentinel
    assert tried == ["93.184.216.34", "93.184.216.35"]


def test_errors_never_carry_a_url() -> None:
    err = httpx.ConnectError("failed for https://other.example.test/up?sig=SECRET now")
    text = delivery._describe(err)
    assert "SECRET" not in text
    assert "<upload_url>" in text


@pytest.mark.parametrize(
    ("kw", "fragment"),
    [
        ({"method": "GET"}, "PUT or POST"),
        ({"headers": {"Host": "x"}}, "cannot be overridden"),
        ({"headers": {"x-a": "1\r\nx-b: 2"}}, "invalid"),
        ({"form_field": "file", "headers": {"Content-Type": "x/y"}}, "multipart"),
    ],
)
async def test_bad_arguments_send_nothing(kw: dict[str, object], fragment: str) -> None:
    with pytest.raises(delivery.DeliveryRefused, match=fragment):
        await _send("https://upload.example.test/x", None, **kw)


# --- save_to ---------------------------------------------------------------


def test_save_to_a_directory_uses_the_files_name(tmp_path: Path) -> None:
    dirs = (str(tmp_path),)
    target = delivery.save_path(str(tmp_path), "../../etc/Läxa.pdf", dirs)
    assert target == tmp_path / "Läxa.pdf"
    delivery.write_new_file(target, PDF, dirs)
    assert target.read_bytes() == PDF
    with pytest.raises(delivery.DeliveryRefused, match="already exists"):
        delivery.write_new_file(target, b"x", dirs)


def test_save_to_relative_lands_in_the_first_dir(tmp_path: Path) -> None:
    dirs = (str(tmp_path),)
    assert delivery.save_path("sub/a.pdf", "x.pdf", dirs) == tmp_path / "sub" / "a.pdf"


@pytest.mark.parametrize("save_to", ["../outside.pdf", "/etc/passwd", "link/x.pdf"])
def test_save_to_outside_is_refused(tmp_path: Path, save_to: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(tmp_path)
    with pytest.raises(delivery.DeliveryRefused, match="SCHOOLSOFT_SAVE_DIRS"):
        delivery.save_path(save_to, "x.pdf", (str(root),))


def test_save_to_is_off_without_dirs() -> None:
    with pytest.raises(delivery.DeliveryRefused, match="switched off"):
        delivery.save_path("/tmp/x.pdf", "x.pdf", ())


# --- the tools --------------------------------------------------------------


class Ctx:
    def __init__(self, app: AppContext) -> None:
        self.request_context = type("R", (), {"lifespan_context": app})()


def _app(tmp_path: Path, allowed: tuple[str, ...] = ALLOWED) -> AppContext:
    settings = Settings(
        school="yourschool",
        username="alice",
        password="secret",
        base_url=BASE,
        upload_allowed_domains=allowed,
        save_dirs=(str(tmp_path),),
    )
    client = SchoolSoftClient(
        school=settings.school,
        username=settings.username,
        password=settings.password,
        base_url=settings.base_url,
    )
    return AppContext(settings=settings, client=client, lock=asyncio.Lock())


def _mock_schoolsoft(content: bytes, router: respx.Router | None = None) -> None:
    mock = router if router is not None else respx.mock
    mock.post(f"{ROOT}/jsp/Login.jsp").mock(
        return_value=httpx.Response(302, headers={"Location": "/yourschool/jsp/start.jsp"})
    )
    mock.get(f"{ROOT}/jsp/start.jsp").mock(return_value=httpx.Response(200, text="ok"))
    mock.get(f"{ROOT}/rest-api/parent/ps/material/{SECTION}/file/{FILE}").mock(
        return_value=httpx.Response(
            200,
            content=content,
            headers={
                "content-type": "application/octet-stream, application/pdf",
                "content-disposition": 'attachment; filename="uppgift.pdf"',
            },
        )
    )


@respx.mock
async def test_download_material_refuses_large_inline_and_points_onward(tmp_path: Path) -> None:
    _mock_schoolsoft(PDF)
    result = await server.download_material(
        Ctx(_app(tmp_path)), section_id=SECTION, file_id=FILE, max_bytes=10_000_000
    )
    assert result.data_base64 == ""
    assert result.size_bytes == len(PDF)
    assert result.note is not None
    assert "upload_material" in result.note
    assert "save_to" in result.note


@respx.mock
async def test_download_material_save_to_returns_only_the_path(tmp_path: Path) -> None:
    _mock_schoolsoft(PDF)
    result = await server.download_material(
        Ctx(_app(tmp_path)), section_id=SECTION, file_id=FILE, save_to=str(tmp_path)
    )
    assert result.data_base64 == ""
    assert result.saved_path == str(tmp_path / "uppgift.pdf")
    assert (tmp_path / "uppgift.pdf").read_bytes() == PDF


async def test_upload_material_refuses_before_downloading(tmp_path: Path) -> None:
    # No SchoolSoft mocks: a refused target must not even fetch the file.
    with pytest.raises(ValueError, match="SCHOOLSOFT_UPLOAD_ALLOWED_DOMAINS is empty"):
        await server.upload_material(
            Ctx(_app(tmp_path, allowed=())),
            section_id=SECTION,
            file_id=FILE,
            upload_url="https://upload.example.test/x",
        )


async def test_upload_material_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    route_to_receiver(monkeypatch)
    with run_receiver() as rx:
        real_upload = delivery.upload

        async def upload_trusting_receiver(**kw: object) -> delivery.UploadResponse:
            return await real_upload(**kw, ssl_context=rx.ssl_context())  # type: ignore[arg-type]

        monkeypatch.setattr(delivery, "upload", upload_trusting_receiver)
        with respx.mock(assert_all_mocked=False) as mock:
            mock.route(host=RECEIVER_HOST).pass_through()
            _mock_schoolsoft(PDF, mock)
            result = await server.upload_material(
                Ctx(_app(tmp_path)),
                section_id=SECTION,
                file_id=FILE,
                upload_url=rx.url(),
            )
    assert result.ok
    assert result.status_code == 201
    assert result.filename == "uppgift.pdf"
    assert result.content_type == "application/pdf"
    assert result.size_bytes == len(PDF)
    assert len(result.response_excerpt) <= 500
    assert "data_base64" not in result.model_dump()
    (got,) = rx.received
    assert got.body == PDF


def _bytes(max_bytes: int, *, save_dirs: tuple[str, ...], upload_hint: bool) -> AttachmentBytes:
    return server._bytes_result(
        PDF,
        {"content-disposition": 'attachment; filename="brev.pdf"'},
        None,
        fallback_name="x",
        max_bytes=max_bytes,
        text_tool="read_attachment_text",
        save_dirs=save_dirs,
        upload_hint=upload_hint,
    )


def test_download_attachment_still_honours_a_larger_max_bytes() -> None:
    # No upload exit for news attachments, so asking for more must still work.
    result = _bytes(10_000_000, save_dirs=(), upload_hint=False)
    assert result.data_base64


def test_the_inline_cap_is_hard_for_material() -> None:
    result = _bytes(10_000_000, save_dirs=(), upload_hint=True)
    assert result.data_base64 == ""


@pytest.mark.parametrize("upload_hint", [True, False])
def test_the_note_says_when_save_to_is_off(upload_hint: bool, tmp_path: Path) -> None:
    off = _bytes(server.MAX_INLINE_BYTES, save_dirs=(), upload_hint=upload_hint)
    assert off.note is not None
    assert "SCHOOLSOFT_SAVE_DIRS" in off.note
    on = _bytes(server.MAX_INLINE_BYTES, save_dirs=(str(tmp_path),), upload_hint=upload_hint)
    assert on.note is not None
    assert "SCHOOLSOFT_SAVE_DIRS" not in on.note
    assert ("max_bytes" in on.note) is not upload_hint


async def _upload_through_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redirects: dict[str, Redirect]
) -> UploadResult:
    route_to_receiver(monkeypatch)
    with run_receiver(redirects) as rx:
        real_upload = delivery.upload

        async def upload_trusting_receiver(**kw: object) -> delivery.UploadResponse:
            return await real_upload(**kw, ssl_context=rx.ssl_context())  # type: ignore[arg-type]

        monkeypatch.setattr(delivery, "upload", upload_trusting_receiver)
        with respx.mock(assert_all_mocked=False) as mock:
            mock.route(host=RECEIVER_HOST).pass_through()
            _mock_schoolsoft(PDF, mock)
            result: UploadResult = await server.upload_material(
                Ctx(_app(tmp_path)),
                section_id=SECTION,
                file_id=FILE,
                upload_url=rx.url(),
                method="POST",
                form_field="file",
            )
    return result


async def test_upload_material_counts_a_303_as_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await _upload_through_tool(tmp_path, monkeypatch, {"/store": (303, "/done")})
    assert result.ok
    assert result.note is not None
    assert "accepted" in result.note


async def test_upload_material_does_not_call_a_302_a_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await _upload_through_tool(tmp_path, monkeypatch, {"/store": (302, "/done")})
    assert not result.ok
    assert result.note is not None
    assert "unknown" in result.note
    assert "not accepted" not in result.note
