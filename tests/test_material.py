"""Teacher-attached material: listed per section, downloaded with Accept */*."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx

from schoolsoft_mcp import server
from schoolsoft_mcp.client import SchoolSoftClient
from schoolsoft_mcp.config import Settings
from schoolsoft_mcp.parsers import subjectrooms as sr
from schoolsoft_mcp.parsers.attachments import filename_from_headers
from schoolsoft_mcp.server import AppContext

BASE = "https://example.test"
ROOT = f"{BASE}/yourschool"
REST = f"{ROOT}/rest-api/parent/ps"
ASSIGNMENT = 900101
SECTION = 900102
FILE = 900103
DOWNLOAD_URL = f"{REST}/material/{SECTION}/file/{FILE}"
SIGNED_URL = f"{BASE}/files/yourschool/tmp_file_{FILE}.tmp"


class Ctx:
    def __init__(self, app: AppContext) -> None:
        self.request_context = type("R", (), {"lifespan_context": app})()


@pytest.fixture
def app() -> AppContext:
    settings = Settings(school="yourschool", username="alice", password="secret", base_url=BASE)
    client = SchoolSoftClient(
        school=settings.school,
        username=settings.username,
        password=settings.password,
        base_url=settings.base_url,
    )
    return AppContext(settings=settings, client=client, lock=asyncio.Lock())


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        server, "_ATTACHMENT_RETRY_DELAYS", (0.0,) * len(server._ATTACHMENT_RETRY_DELAYS)
    )
    monkeypatch.setattr(server, "_SIGNED_URL_RETRY_DELAY", 0.0)


def _mock_login() -> None:
    respx.post(f"{ROOT}/jsp/Login.jsp").mock(
        return_value=httpx.Response(302, headers={"Location": "/yourschool/jsp/start.jsp"})
    )
    respx.get(f"{ROOT}/jsp/start.jsp").mock(return_value=httpx.Response(200, text="ok"))


def _download_route(final: httpx.Response) -> respx.Route:
    """The REST download: 406 to an HTML Accept, else 303 to the signed copy."""

    def answer(request: httpx.Request) -> httpx.Response:
        if "text/html" in request.headers.get("accept", ""):
            return httpx.Response(406, json="not acceptable")
        return httpx.Response(303, headers={"Location": SIGNED_URL})

    route = respx.get(DOWNLOAD_URL).mock(side_effect=answer)
    respx.get(url__startswith=SIGNED_URL).mock(return_value=final)
    return route


class TestParsing:
    def test_section_ids(self) -> None:
        payload = [{"id": SECTION, "type": "DESCRIPTION"}, {"type": "x"}, "junk"]
        assert sr.parse_section_ids(payload) == [SECTION]
        assert sr.parse_section_ids(None) == []

    def test_file_entry_shape(self) -> None:
        material = sr.parse_material(
            [{"fileId": FILE, "fileName": "Glosor vecka 3.pdf", "fileSize": 1234}],
            [],
            section_id=SECTION,
        )
        assert len(material) == 1
        entry = material[0]
        assert (entry.kind, entry.name, entry.file_id) == ("file", "Glosor vecka 3.pdf", FILE)
        assert (entry.section_id, entry.size_bytes, entry.url) == (SECTION, 1234, None)

    @pytest.mark.parametrize(
        ("disposition", "expected"),
        [
            ('attachment; filename="Information; prov.pdf"', "Information; prov.pdf"),
            ("attachment; filename=plain.pdf", "plain.pdf"),
            ("attachment; filename*=UTF-8''%C3%A5r%207.pdf", "år 7.pdf"),
            # httpx hands a UTF-8 header over decoded as latin-1.
            ('attachment; filename="Ã¥r 7.pdf"', "år 7.pdf"),
            ('attachment; filename="café.pdf"', "café.pdf"),
            ("inline", "fallback"),
        ],
    )
    def test_filename_from_headers(self, disposition: str, expected: str) -> None:
        headers = {"content-disposition": disposition}
        assert filename_from_headers(headers, "fallback") == expected

    def test_content_type_takes_the_specific_entry(self) -> None:
        headers = {"content-type": "application/octet-stream, application/pdf"}
        assert server._content_type(headers, "x.bin") == "application/pdf"
        assert server._content_type({}, "x.docx").endswith("wordprocessingml.document")
        assert server._content_type({"content-type": "text/plain; charset=utf-8"}, "x") == (
            "text/plain"
        )


@respx.mock
async def test_read_material_text_sends_accept_any(app: AppContext) -> None:
    _mock_login()
    route = _download_route(
        httpx.Response(
            200,
            content="Läs kapitel 4.".encode(),
            headers={"content-type": "application/octet-stream, text/plain"},
        )
    )
    result = await server.read_material_text(Ctx(app), section_id=SECTION, file_id=FILE)
    assert result.note is None
    assert result.text == "Läs kapitel 4."
    assert result.content_type == "text/plain"
    assert route.calls.last.request.headers["accept"] == "*/*"


@respx.mock
async def test_download_material_returns_bytes(app: AppContext) -> None:
    _mock_login()
    _download_route(
        httpx.Response(200, content=b"%PDF-1.4 stub", headers={"content-type": "application/pdf"})
    )
    result = await server.download_material(Ctx(app), section_id=SECTION, file_id=FILE)
    assert result.note is None
    assert result.size_bytes == len(b"%PDF-1.4 stub")
    assert result.content_type == "application/pdf"


@respx.mock
async def test_a_flaky_signed_url_is_asked_again(app: AppContext) -> None:
    # The same signed URL answers 404 and 200 at random; asking again is
    # what works, not minting a new one.
    _mock_login()
    rest = _download_route(httpx.Response(200))
    signed = respx.get(url__startswith=SIGNED_URL).mock(
        side_effect=[
            httpx.Response(404),
            httpx.Response(404),
            httpx.Response(200, content=b"ok", headers={"content-type": "text/plain"}),
        ]
    )
    result = await server.read_material_text(Ctx(app), section_id=SECTION, file_id=FILE)
    assert result.note is None
    assert result.text == "ok"
    assert (rest.call_count, signed.call_count) == (1, 3)


@respx.mock
async def test_a_file_without_text_says_so(app: AppContext) -> None:
    _mock_login()
    _download_route(
        httpx.Response(200, content=b"   ", headers={"content-type": "text/plain"})
    )
    result = await server.read_material_text(Ctx(app), section_id=SECTION, file_id=FILE)
    assert result.note is not None
    assert "no extractable text" in result.note


@respx.mock
async def test_missing_file_retries_then_explains(app: AppContext) -> None:
    _mock_login()
    route = respx.get(DOWNLOAD_URL).mock(return_value=httpx.Response(404))
    result = await server.read_material_text(Ctx(app), section_id=SECTION, file_id=FILE)
    assert route.call_count == len(server._ATTACHMENT_RETRY_DELAYS)
    assert result.text == ""
    assert result.note is not None
    assert "HTTP 404" in result.note
    assert "student_id" in result.note


def _assignment_row() -> dict[str, Any]:
    return {
        "assignmentId": ASSIGNMENT,
        "activityId": 900104,
        "assignmentType": "HOMEWORK",
        "title": "Påhittad läxa",
        "subjects": [{"name": "Engelska", "color": ""}],
        "teacher": "Alex Andersson",
        "startDate": "2026-11-16",
        "endDate": "2026-11-20",
        "publishDate": "2026-11-13 08:00",
        "status": "ONGOING",
        "read": True,
        "submissionStatus": None,
        "resultReportStatus": None,
    }


@respx.mock
async def test_homework_lists_material_only_when_asked(app: AppContext) -> None:
    _mock_login()
    respx.get(f"{REST}/subjectroom/assignments/grid/rows").mock(
        return_value=httpx.Response(200, json=[_assignment_row()])
    )
    sections = respx.get(f"{REST}/assignments/{ASSIGNMENT}/sections").mock(
        return_value=httpx.Response(200, json=[{"id": SECTION, "type": "DESCRIPTION"}])
    )
    respx.get(f"{REST}/material/{SECTION}/file").mock(
        return_value=httpx.Response(
            200, json=[{"fileId": FILE, "fileName": "Glosor.pdf", "fileSize": 10}]
        )
    )
    respx.get(f"{REST}/material/{SECTION}/link").mock(
        return_value=httpx.Response(
            200, json=[{"id": 900105, "displayName": "Övning", "url": "https://example.org/x"}]
        )
    )
    ctx = Ctx(app)

    plain = await server.get_homework(ctx, week=47, year=2026, include_body=False)
    assert sections.call_count == 0
    assert plain.items[0].material == []

    with_material = await server.get_homework(
        ctx, week=47, year=2026, include_body=False, include_material=True
    )
    material = with_material.items[0].material
    assert [(m.kind, m.section_id) for m in material] == [("file", SECTION), ("link", SECTION)]
    assert material[0].file_id == FILE
