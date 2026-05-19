"""Unit tests for attachment text extraction routing."""

from __future__ import annotations

from schoolsoft_mcp.parsers.attachments import (
    _looks_like_docx,
    _parse_content_types,
    extract_text,
)

PDF_MAGIC = b"%PDF-1.4\n%example minimal stub\n"
DOCX_ZIP_WITH_WORD = b"PK\x03\x04" + b"\x00" * 30 + b"word/document.xml" + b"\x00" * 200
PLAIN_ZIP = b"PK\x03\x04" + b"\x00" * 200  # no "word/" marker


def test_parse_content_types_handles_comma_separated() -> None:
    """SchoolSoft's redirect chain produces comma-joined Content-Type headers."""
    assert _parse_content_types("application/octet-stream, application/pdf") == [
        "application/octet-stream",
        "application/pdf",
    ]


def test_parse_content_types_handles_parameters() -> None:
    """Strip the ``;charset=...`` form too."""
    assert _parse_content_types("text/html; charset=utf-8") == ["text/html"]


def test_parse_content_types_combined() -> None:
    """Mix of comma + semicolon (rare but legal)."""
    assert _parse_content_types(
        "application/octet-stream, application/vnd.openxmlformats-officedocument.wordprocessingml.document; charset=utf-8"
    ) == [
        "application/octet-stream",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]


def test_looks_like_docx_positive() -> None:
    assert _looks_like_docx(DOCX_ZIP_WITH_WORD) is True


def test_looks_like_docx_negative() -> None:
    assert _looks_like_docx(PLAIN_ZIP) is False


def test_extract_text_routes_pdf_by_magic_bytes() -> None:
    """Even with a comma-joined Content-Type, magic bytes win."""
    text, _truncated, note = extract_text(
        PDF_MAGIC, "application/octet-stream, application/pdf"
    )
    # We don't expect actual extracted text from a non-PDF byte string,
    # but routing should reach the PDF extractor — so the note should
    # describe a PDF parse failure rather than "no text extractor".
    assert "No text extractor" not in (note or "")
    assert isinstance(text, str)


def test_extract_text_returns_note_for_unknown_content_type() -> None:
    text, _truncated, note = extract_text(b"random bytes", "image/jpeg")
    assert text == ""
    assert note is not None
    assert "image/jpeg" in note


def test_extract_text_decodes_plain_text() -> None:
    body = "Hello — bü"
    text, _truncated, note = extract_text(body.encode("utf-8"), "text/plain; charset=utf-8")
    assert text == body
    assert note is None


def test_extract_text_decodes_text_with_comma_joined_header() -> None:
    body = "ok"
    text, _truncated, note = extract_text(
        body.encode("utf-8"), "application/octet-stream, text/plain"
    )
    assert text == body
    assert note is None
