"""Attachment download + text extraction.

SchoolSoft serves news/message attachments through a two-step flow:

1. ``jsp/student/right_student_file_download.jsp?requestid1=<news_id>
   &requestid2=<type_id>&object=<news|message>&fileid=<file_id>``
   returns a 302 to
2. ``/files/<school>/tmp_file_<file_id>.tmp?md5=<sig>&expires=<ts>&filename=<name>``
   which is the actual file. The signed URL expires after a few minutes.

We hide that two-step dance behind ``download_attachment_bytes`` —
``SchoolSoftClient.fetch_bytes`` follows the redirect transparently.
"""

from __future__ import annotations

import io
import logging
import mimetypes
import re
from urllib.parse import unquote

logger = logging.getLogger(__name__)

# Hard cap on text extraction output. LLM contexts are finite; PDFs of
# hundreds of pages would be useless and expensive. Configurable per-call.
DEFAULT_TEXT_LIMIT = 50_000

ObjectKind = str  # "news" | "message"


def build_download_path(
    *,
    parent_id: int,
    type_id: int,
    fileid: int,
    object_kind: ObjectKind = "news",
) -> tuple[str, dict[str, str]]:
    """Return (path, params) for SchoolSoftClient.fetch_bytes."""
    return (
        "jsp/student/right_student_file_download.jsp",
        {
            "requestid1": str(parent_id),
            "requestid2": str(type_id),
            "object": object_kind,
            "fileid": str(fileid),
        },
    )


def guess_content_type(filename: str) -> str:
    """Best-effort content-type from filename. Falls back to octet-stream."""
    ctype, _ = mimetypes.guess_type(filename)
    return ctype or "application/octet-stream"


def filename_from_headers(headers: dict[str, str], fallback: str) -> str:
    """Extract a clean filename from a Content-Disposition header.

    Returns ``fallback`` when the header is missing or unparseable.
    Callers that want a URL-based fallback (e.g. the ``filename=`` query
    param on SchoolSoft's signed download URLs) should pass that as
    ``fallback`` themselves.
    """
    cd = headers.get("content-disposition", "")
    if not cd:
        return fallback
    # filename*=UTF-8''url-encoded wins over filename=...
    star = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", cd, re.IGNORECASE)
    if star:
        return unquote(star.group(1).strip().strip('"'))
    plain = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.IGNORECASE)
    if plain:
        return plain.group(1).strip()
    return fallback


def extract_text(
    content: bytes,
    content_type: str,
    *,
    limit: int = DEFAULT_TEXT_LIMIT,
) -> tuple[str, bool, str | None]:
    """Best-effort plain-text extraction. Returns (text, truncated, note).

    Supports PDF (via pypdf), .docx (via python-docx), and plain text. For
    other content types the note explains what to do (typically: use
    download_attachment to get the raw bytes).
    """
    ctype = content_type.split(";")[0].strip().lower()

    if ctype in {"application/pdf"} or content[:4] == b"%PDF":
        return _truncate(_extract_pdf(content), limit)
    if (
        ctype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or (ctype == "application/octet-stream" and content[:4] == b"PK\x03\x04")
    ):
        return _truncate(_extract_docx(content), limit)
    if ctype.startswith("text/") or ctype in {"application/json", "application/xml"}:
        try:
            return _truncate(content.decode("utf-8"), limit)
        except UnicodeDecodeError:
            return _truncate(content.decode("latin-1", errors="replace"), limit)

    note = (
        f"No text extractor for content-type {ctype!r}. "
        "Use download_attachment to fetch the raw bytes."
    )
    return "", False, note


def _truncate(text: str, limit: int) -> tuple[str, bool, str | None]:
    if len(text) <= limit:
        return text, False, None
    return (
        text[:limit] + f"\n\n... [truncated, {len(text) - limit} chars omitted]",
        True,
        None,
    )


def _extract_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return (
            "[pypdf not installed; reinstall the package to enable PDF text "
            "extraction: pip install -e .]"
        )

    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as err:
        logger.warning("PDF parse failed: %s", err)
        return f"[Could not parse PDF: {err}]"

    parts: list[str] = []
    for page_num, page in enumerate(reader.pages, start=1):
        try:
            parts.append(page.extract_text() or "")
        except Exception as err:
            logger.warning("PDF page %d extract failed: %s", page_num, err)
            parts.append(f"[page {page_num}: extraction failed]")
    return "\n\n".join(p for p in parts if p.strip())


def _extract_docx(content: bytes) -> str:
    try:
        from docx import Document
    except ImportError:
        return (
            "[python-docx not installed; reinstall the package to enable "
            ".docx text extraction: pip install -e .]"
        )

    try:
        doc = Document(io.BytesIO(content))
    except Exception as err:
        logger.warning("DOCX parse failed: %s", err)
        return f"[Could not parse DOCX: {err}]"

    lines = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines)
