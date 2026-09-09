"""Tests for the accordion-group news parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from schoolsoft_mcp.parsers.news import parse_news

FIXTURE = Path(__file__).parent / "fixtures" / "news_accordion.html"


@pytest.fixture
def news_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parses_all_items(news_html: str) -> None:
    feed = parse_news(news_html, school="yourschool")
    assert feed.note is None
    assert len(feed.items) == 2
    titles = [item.title for item in feed.items]
    assert "Studiedag 12 maj" in titles
    assert "Veckobrev v 19" in titles


def test_news_id_extracted_from_accordion_group(news_html: str) -> None:
    feed = parse_news(news_html, school="yourschool")
    ids = sorted(item.news_id for item in feed.items if item.news_id is not None)
    assert ids == [900008, 900013]


def test_type_id_comes_from_form_action(news_html: str) -> None:
    """The form action's ?type= overrides the default. Fixes Copilot review #1."""
    feed = parse_news(news_html, school="yourschool", default_type_id=1)
    by_id = {item.news_id: item for item in feed.items}
    # First item's form action has type=1 — matches default.
    assert by_id[900008].type_id == 1
    # Second item's form action has type=2 — overrides default.
    assert by_id[900013].type_id == 2


def test_default_type_id_used_when_form_missing() -> None:
    """If no form has a parseable type, fall back to the caller's default."""
    minimal_html = """
    <div class="accordion-group" id="accordion-group99">
      <span id="name99">Plain item</span>
    </div>
    """
    feed = parse_news(minimal_html, school="yourschool", default_type_id=2)
    assert len(feed.items) == 1
    assert feed.items[0].type_id == 2


def test_category_section_heading(news_html: str) -> None:
    feed = parse_news(news_html, school="yourschool")
    by_id = {item.news_id: item for item in feed.items}
    assert by_id[900008].category == "ALLMÄN information"
    assert by_id[900013].category == "VECKOBREV"


def test_body_preserves_paragraphs(news_html: str) -> None:
    feed = parse_news(news_html, school="yourschool")
    studiedag = next(i for i in feed.items if i.news_id == 900008)
    assert "Skolan stänger 13:00" in studiedag.body
    assert "Lunch serveras som vanligt" in studiedag.body
    # Paragraphs are joined with blank lines.
    assert "\n\n" in studiedag.body


def test_meta_block_extracted(news_html: str) -> None:
    feed = parse_news(news_html, school="yourschool")
    studiedag = next(i for i in feed.items if i.news_id == 900008)
    assert studiedag.author == "Alice Andersson (P)"
    assert studiedag.recipient.startswith("6")
    assert studiedag.published == "10 maj"
    assert studiedag.visible_until == "15 maj"


def test_attachments_parsed_with_clean_filenames(news_html: str) -> None:
    feed = parse_news(news_html, school="yourschool")
    veckobrev = next(i for i in feed.items if i.news_id == 900013)
    assert len(veckobrev.attachments) == 2

    fileids = sorted(a.fileid for a in veckobrev.attachments)
    assert fileids == [4040, 4041]

    by_fileid = {a.fileid: a for a in veckobrev.attachments}
    # The clean filename comes from the anchor's title= attribute,
    # not the truncated displayed link text.
    assert by_fileid[4040].filename == "Veckobrev v19.pdf"
    assert by_fileid[4041].filename == "Schemaändringar.docx"


def test_attachment_size_parsed_with_nbsp(news_html: str) -> None:
    feed = parse_news(news_html, school="yourschool")
    veckobrev = next(i for i in feed.items if i.news_id == 900013)
    by_fileid = {a.fileid: a for a in veckobrev.attachments}
    # "141 KB" with a non-breaking space between number and unit.
    assert by_fileid[4040].size_bytes == 141 * 1024
    assert by_fileid[4041].size_bytes == 12 * 1024


def test_attachment_content_type_guessed_from_extension(news_html: str) -> None:
    feed = parse_news(news_html, school="yourschool")
    veckobrev = next(i for i in feed.items if i.news_id == 900013)
    by_fileid = {a.fileid: a for a in veckobrev.attachments}
    assert by_fileid[4040].content_type == "application/pdf"
    assert (
        by_fileid[4041].content_type
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_empty_html_returns_note() -> None:
    feed = parse_news("<html><body></body></html>", school="yourschool")
    assert feed.items == []
    assert feed.note is not None
    assert "no items" in feed.note.lower()
