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
    # Every row on this fixture is still awaiting acknowledgement, so the
    # note says how many rather than being absent.
    assert u.note is not None
    assert "awaiting a guardian" in u.note
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




# ---------------------------------------------------------------------------
# Acknowledgement: SchoolSoft texts a guardian, who must then confirm they
# saw it. Confirmed rows stay on the page for good.
# ---------------------------------------------------------------------------


@pytest.fixture
def acknowledged_html() -> str:
    return (FIXTURES / "absence_message_acknowledged.html").read_text(encoding="utf-8")


@pytest.fixture
def mixed_html() -> str:
    return (FIXTURES / "absence_message_mixed.html").read_text(encoding="utf-8")


def test_acknowledged_rows_are_not_reported_as_outstanding(
    acknowledged_html: str,
) -> None:
    # The exact live failure: the page says nothing is outstanding, but the
    # already-confirmed table has the same columns, so a positional parser
    # reported two weeks-old absences as new every single morning.
    u = parse_unreported_absence(acknowledged_html, school="X")
    assert u.events == []
    assert len(u.acknowledged) == 2
    assert u.confirmed_none_pending is True


def test_who_and_when_are_kept(acknowledged_html: str) -> None:
    u = parse_unreported_absence(acknowledged_html, school="X")
    first = u.acknowledged[0]
    assert first.acknowledged_by == "Alex Andersson"
    assert first.acknowledged_at == "2026-09-09 7:11"
    assert first.week == 35
    assert first.lesson == "13:45-14:20 MA"
    assert first.school_confirmed == ""


def test_a_page_with_both_tables_splits_them(mixed_html: str) -> None:
    u = parse_unreported_absence(mixed_html, school="X")
    assert [e.lesson for e in u.events] == ["8:20-9:20 ID"]
    assert [e.lesson for e in u.acknowledged] == ["13:45-14:20 MA"]
    assert u.confirmed_none_pending is False
    assert u.note is not None and "1 unreported absence" in u.note


def test_the_message_column_is_not_mistaken_for_acknowledgement(
    mixed_html: str,
) -> None:
    # "SMS skickades 2026-09-10 08:25" also ends in a timestamp; only the
    # "Tagit del av" column counts as a guardian having confirmed.
    pending = parse_unreported_absence(mixed_html, school="X").events[0]
    assert pending.acknowledged_by == ""
    assert pending.acknowledged_at == ""
    assert pending.message.startswith("SMS skickades")


def test_nothing_parsed_is_distinct_from_nothing_pending() -> None:
    u = parse_unreported_absence("<html><body></body></html>", school="X")
    assert u.events == []
    assert u.confirmed_none_pending is False
    assert u.note is not None and "did not state" in u.note
