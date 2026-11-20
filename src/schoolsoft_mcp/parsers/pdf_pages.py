"""Render document pages as images, for scans that hold no text layer.

A scanned page is a picture: ``extract_text`` finds nothing in it, and
returning an empty string hides a worksheet the model could simply read.
The MCP client's model reads images, so the fallback is to hand it the pages.

Pages are rendered with PDFium rather than lifted out of the PDF, because
school scanners compress black-and-white pages with JBIG2, which pypdf
cannot decode without an external ``jbig2dec`` binary. Rendering also covers
PDFs whose text is drawn as vector outlines.
"""

from __future__ import annotations

import importlib
import io
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _load_pdfium() -> Any | None:
    """pypdfium2, or None when an install predates the dependency."""
    try:
        return importlib.import_module("pypdfium2")
    except ImportError:  # pragma: no cover - declared dependency
        return None


# Loaded at module load for the same reason as pypdf in attachments.py: a
# first import inside a tool call has been slow enough on Windows for the
# client to cancel the call.
_pdfium = _load_pdfium()

# Claude scales any image with a longer edge than this down before reading
# it, so rendering larger costs bytes and buys nothing.
LONG_EDGE_PX = 1568
JPEG_QUALITY = 70
# Leaves room for base64 (+33 %) under the ~1 MB tool-result cap. A scanned
# A4 page at the size above is about 80 KB.
DEFAULT_BYTE_BUDGET = 650_000

_IMAGE_FORMATS = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


@dataclass
class RenderedPages:
    """Pages ``first_page``..``first_page + len(images) - 1`` (1-based)."""

    images: list[bytes] = field(default_factory=list)
    image_format: str = "jpeg"
    first_page: int = 1
    total_pages: int = 0
    next_page: int | None = None
    note: str | None = None


def render_pages(
    content: bytes,
    content_type: str,
    *,
    start_page: int = 1,
    max_pages: int = 5,
    byte_budget: int = DEFAULT_BYTE_BUDGET,
) -> RenderedPages:
    """Render up to ``max_pages`` pages from ``start_page`` as JPEG.

    Stops early once the next page would push the total past
    ``byte_budget``; ``next_page`` then says where to continue. An image
    file is passed through as its single page.
    """
    image_format = _IMAGE_FORMATS.get(content_type)
    if image_format is not None:
        if len(content) > byte_budget:
            return RenderedPages(
                total_pages=1,
                note=f"The image is {len(content):,} bytes, over the "
                f"{byte_budget:,}-byte budget for one tool result.",
            )
        return RenderedPages(images=[content], image_format=image_format, total_pages=1)

    if content_type != "application/pdf" and not content.startswith(b"%PDF"):
        return RenderedPages(
            note=f"Cannot show {content_type} as pages — only PDFs and images."
        )
    if _pdfium is None:
        return RenderedPages(note="pypdfium2 is not installed, so pages cannot be rendered.")

    try:
        document = _pdfium.PdfDocument(content)
    except _pdfium.PdfiumError as err:
        return RenderedPages(note=f"The PDF could not be opened: {err}")

    try:
        total = len(document)
        if not 1 <= start_page <= total:
            return RenderedPages(
                total_pages=total,
                note=f"start_page {start_page} is outside the document's pages 1-{total}.",
            )
        images: list[bytes] = []
        used = 0
        page_no = start_page
        while page_no <= total and len(images) < max_pages:
            data = _render_page(document[page_no - 1])
            if images and used + len(data) > byte_budget:
                break
            images.append(data)
            used += len(data)
            page_no += 1
    finally:
        document.close()

    return RenderedPages(
        images=images,
        first_page=start_page,
        total_pages=total,
        next_page=page_no if page_no <= total else None,
    )


def _render_page(page: Any) -> bytes:
    try:
        width, height = page.get_size()
        bitmap = page.render(scale=LONG_EDGE_PX / max(width, height))
        image = bitmap.to_pil().convert("RGB")
    finally:
        page.close()
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()
