"""Tests for the grades parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from schoolsoft_mcp.parsers.grades import parse_grades

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def grades_html() -> str:
    return (FIXTURES / "grades.html").read_text(encoding="utf-8")


def test_terms_collected_in_order(grades_html: str) -> None:
    g = parse_grades(grades_html, school="X")
    assert g.terms == ["24/25 Vt", "25/26 Ht"]


def test_one_entry_per_subject_term_pair(grades_html: str) -> None:
    g = parse_grades(grades_html, school="X")
    # Bild has 2 grades (one per term).
    bild = [e for e in g.grades if e.subject == "Bild"]
    assert {e.term: e.grade for e in bild} == {"24/25 Vt": "B", "25/26 Ht": "C"}


def test_partial_grades_kept(grades_html: str) -> None:
    """Idrott has no Vt grade — only the Ht entry is emitted."""
    g = parse_grades(grades_html, school="X")
    idrott = [e for e in g.grades if e.subject == "Idrott och hälsa"]
    assert len(idrott) == 1
    assert idrott[0].term == "25/26 Ht"
    assert idrott[0].grade == "E"


def test_fully_empty_subject_skipped(grades_html: str) -> None:
    """Teknik has no grade in any term and no note — yields zero entries."""
    g = parse_grades(grades_html, school="X")
    assert not any(e.subject == "Teknik" for e in g.grades)


def test_note_propagated_to_all_terms_for_that_subject(grades_html: str) -> None:
    """The Notering column lives at the row level, so it applies to every
    (subject, term) entry from that row."""
    g = parse_grades(grades_html, school="X")
    matte = [e for e in g.grades if e.subject == "Matematik"]
    assert len(matte) == 2
    assert all(e.note.startswith("Behöver stötta") for e in matte)


def test_empty_html_returns_note() -> None:
    g = parse_grades("<html><body></body></html>", school="X")
    assert g.grades == []
    assert g.note is not None
    assert "no grades" in g.note.lower()


def test_school_propagated(grades_html: str) -> None:
    g = parse_grades(grades_html, school="yourschool")
    assert g.school == "yourschool"
