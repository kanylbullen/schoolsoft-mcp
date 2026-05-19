"""Tests for the attendance + unreported-absence parsers."""

from __future__ import annotations

from pathlib import Path

import pytest

from schoolsoft_mcp.parsers.attendance import (
    parse_attendance,
    parse_unreported_absence,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def rapport_html() -> str:
    return (FIXTURES / "absence_rapport.html").read_text(encoding="utf-8")


@pytest.fixture
def unreported_html() -> str:
    return (FIXTURES / "absence_message.html").read_text(encoding="utf-8")


# --- Frånvaro → Rapport -------------------------------------------------------


def test_attendance_parses_all_weeks(rapport_html: str) -> None:
    r = parse_attendance(rapport_html, school="X")
    assert r.note is None
    assert [w.week for w in r.weeks] == [2, 3, 17]


def test_attendance_extracts_counts_and_percentages(rapport_html: str) -> None:
    r = parse_attendance(rapport_html, school="X")
    by_week = {w.week: w for w in r.weeks}
    perfect = by_week[2]
    assert perfect.total_present_count == 11
    assert perfect.total_present_percent == 100.0
    assert perfect.unreported_absence_count == 0
    assert perfect.unreported_absence_percent == 0.0
    assert perfect.reported_absence_count == 0
    assert perfect.reported_absence_percent == 0.0
    assert perfect.present == 11


def test_attendance_extracts_sub_counts(rapport_html: str) -> None:
    r = parse_attendance(rapport_html, school="X")
    by_week = {w.week: w for w in r.weeks}
    v17 = by_week[17]
    assert v17.late_arrival == 2
    assert v17.preregistered == 16
    assert v17.reported_absence_count == 18
    assert v17.reported_absence_percent == 72.0


def test_attendance_dash_cells_become_zero(rapport_html: str) -> None:
    """`-` cells render to 0 in the model (sub-counts), not None."""
    r = parse_attendance(rapport_html, school="X")
    by_week = {w.week: w for w in r.weeks}
    assert by_week[2].absent == 0
    assert by_week[2].leave_granted == 0


def test_attendance_empty_returns_note() -> None:
    r = parse_attendance("<html><body></body></html>", school="X")
    assert r.weeks == []
    assert r.note is not None
    assert "no weekly rows" in r.note.lower()


# --- Frånvaro → Oanmäld frånvaro --------------------------------------------


def test_unreported_parses_all_rows(unreported_html: str) -> None:
    u = parse_unreported_absence(unreported_html, school="X")
    assert u.note is None
    assert len(u.events) == 5


def test_unreported_forward_fills_week_and_day(unreported_html: str) -> None:
    """SchoolSoft uses &nbsp; in week/day cells to mean 'same as previous'."""
    u = parse_unreported_absence(unreported_html, school="X")
    # First three rows all belong to v37 / Onsdag, even though only the
    # first row has those cells populated.
    v37 = [e for e in u.events if e.week == 37]
    assert len(v37) == 3
    assert all(e.day == "Onsdag" for e in v37)


def test_unreported_extracts_lesson_and_message(unreported_html: str) -> None:
    u = parse_unreported_absence(unreported_html, school="X")
    sms_rows = [e for e in u.events if e.message == "SMS skickades"]
    assert len(sms_rows) == 2
    correction = next(e for e in u.events if e.message == "Korrigerad anmälan")
    assert correction.week == 39
    assert correction.day == "Torsdag"
    assert correction.lesson == "9:20-10:00 MA"


def test_unreported_empty_returns_note() -> None:
    u = parse_unreported_absence("<html><body></body></html>", school="X")
    assert u.events == []
    assert u.note is not None
    assert "no unreported" in u.note.lower()
