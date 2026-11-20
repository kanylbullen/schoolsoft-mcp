"""Page rendering for scans: budget, paging, passthrough, and the tool shape."""

from __future__ import annotations

import asyncio
import io

import httpx
import pypdfium2 as pdfium
import pytest
import respx
from mcp.server.fastmcp import Image

from schoolsoft_mcp import server
from schoolsoft_mcp.client import SchoolSoftClient
from schoolsoft_mcp.config import Settings
from schoolsoft_mcp.parsers.pdf_pages import LONG_EDGE_PX, render_pages
from schoolsoft_mcp.server import AppContext

BASE = "https://example.test"
ROOT = f"{BASE}/yourschool"
SECTION = 900201
FILE = 900202
DOWNLOAD_URL = f"{ROOT}/rest-api/parent/ps/material/{SECTION}/file/{FILE}"
SIGNED_URL = f"{BASE}/files/yourschool/tmp_file_{FILE}.tmp"


def blank_pdf(pages: int) -> bytes:
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(595, 842)  # A4 in points
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()


class TestRenderPages:
    def test_renders_jpeg_at_the_model_size(self) -> None:
        result = render_pages(blank_pdf(2), "application/pdf")
        assert result.note is None
        assert (result.total_pages, len(result.images), result.next_page) == (2, 2, None)
        assert all(image.startswith(b"\xff\xd8") for image in result.images)
        rendered = pdfium.PdfDocument(blank_pdf(1))  # same page size
        width, height = rendered[0].get_size()
        assert round(LONG_EDGE_PX / max(width, height) * height) == LONG_EDGE_PX
        rendered.close()

    def test_max_pages_and_next_page(self) -> None:
        result = render_pages(blank_pdf(4), "application/pdf", start_page=2, max_pages=2)
        assert (result.first_page, len(result.images), result.next_page) == (2, 2, 4)

    def test_byte_budget_stops_early_but_keeps_one_page(self) -> None:
        result = render_pages(blank_pdf(3), "application/pdf", byte_budget=1)
        assert (len(result.images), result.next_page) == (1, 2)

    def test_start_page_out_of_range(self) -> None:
        result = render_pages(blank_pdf(1), "application/pdf", start_page=3)
        assert result.images == []
        assert result.note is not None
        assert "pages 1-1" in result.note

    def test_detects_pdf_by_magic_when_mislabelled(self) -> None:
        result = render_pages(blank_pdf(1), "application/octet-stream")
        assert len(result.images) == 1

    def test_image_passes_through(self) -> None:
        result = render_pages(b"\x89PNG stub", "image/png")
        assert (result.images, result.image_format) == ([b"\x89PNG stub"], "png")

    def test_other_types_are_refused(self) -> None:
        result = render_pages(b"text", "text/plain")
        assert result.images == []
        assert result.note is not None


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


def _mock_download(content: bytes) -> None:
    respx.post(f"{ROOT}/jsp/Login.jsp").mock(
        return_value=httpx.Response(302, headers={"Location": "/yourschool/jsp/start.jsp"})
    )
    respx.get(f"{ROOT}/jsp/start.jsp").mock(return_value=httpx.Response(200, text="ok"))
    respx.get(DOWNLOAD_URL).mock(
        return_value=httpx.Response(303, headers={"Location": SIGNED_URL})
    )
    respx.get(url__startswith=SIGNED_URL).mock(
        return_value=httpx.Response(
            200,
            content=content,
            headers={
                "content-type": "application/octet-stream, application/pdf",
                "content-disposition": 'attachment; filename="Arbetsblad.pdf"',
            },
        )
    )


@respx.mock
async def test_view_material_pages_returns_summary_then_images(app: AppContext) -> None:
    _mock_download(blank_pdf(2))
    result = await server.view_material_pages(Ctx(app), section_id=SECTION, file_id=FILE)
    summary, *images = result
    assert isinstance(summary, str)
    assert summary.startswith("Arbetsblad.pdf: pages 1-2 of 2")
    assert len(images) == 2
    assert all(isinstance(image, Image) for image in images)


@respx.mock
async def test_a_scan_points_the_text_tool_at_the_pages_tool(app: AppContext) -> None:
    _mock_download(blank_pdf(1))
    result = await server.read_material_text(Ctx(app), section_id=SECTION, file_id=FILE)
    assert result.text == ""
    assert result.note is not None
    assert "view_material_pages" in result.note
