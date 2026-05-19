"""Tests for the REST schedule parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from schoolsoft_mcp.parsers.schedule import parse_schedule_json

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def lessons_json() -> list[dict]:  # type: ignore[type-arg]
    return json.loads(
        (FIXTURES / "calendar_lessons_week.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def events_json() -> list[dict]:  # type: ignore[type-arg]
    return json.loads(
        (FIXTURES / "calendar_events_week.json").read_text(encoding="utf-8")
    )


def test_parses_all_lessons(lessons_json: list[dict]) -> None:  # type: ignore[type-arg]
    s = parse_schedule_json(lessons_json, school="X", week=21, year=2026)
    assert s.note is None
    assert s.week == 21
    assert s.year == 2026
    assert len(s.lessons) == 3


def test_day_id_maps_to_weekday(lessons_json: list[dict]) -> None:  # type: ignore[type-arg]
    """dayId 0..4 → monday..friday."""
    s = parse_schedule_json(lessons_json, school="X")
    days = {lesson.subject: lesson.day for lesson in s.lessons}
    assert days["Slöjd"] == "monday"  # dayId=0
    assert days["MA"] == "thursday"  # dayId=3


def test_start_end_times_formatted(lessons_json: list[dict]) -> None:  # type: ignore[type-arg]
    s = parse_schedule_json(lessons_json, school="X")
    slojd = next(lesson for lesson in s.lessons if lesson.subject == "Slöjd")
    assert slojd.start == "08:50"
    assert slojd.end == "10:00"


def test_room_and_teacher_propagated(lessons_json: list[dict]) -> None:  # type: ignore[type-arg]
    s = parse_schedule_json(lessons_json, school="X")
    slojd = next(lesson for lesson in s.lessons if lesson.subject == "Slöjd")
    assert slojd.room == "slöjdsal"
    assert slojd.teacher == "Carl Carlsson"
    assert slojd.teaching_group == "6"


def test_is_break_detected(lessons_json: list[dict]) -> None:  # type: ignore[type-arg]
    s = parse_schedule_json(lessons_json, school="X")
    rast = next(lesson for lesson in s.lessons if lesson.subject == "Rast")
    assert rast.is_break is True
    other = next(lesson for lesson in s.lessons if lesson.subject != "Rast")
    assert other.is_break is False


def test_notes_only_when_description_differs(lessons_json: list[dict]) -> None:  # type: ignore[type-arg]
    """``description`` mirrors ``name`` most of the time; ``notes`` only when
    it adds something."""
    s = parse_schedule_json(lessons_json, school="X")
    ma = next(lesson for lesson in s.lessons if lesson.subject == "MA")
    assert ma.notes == "MA — Diagnos"
    rast = next(lesson for lesson in s.lessons if lesson.subject == "Rast")
    assert rast.notes == ""


def test_attendance_status_extracted(lessons_json: list[dict]) -> None:  # type: ignore[type-arg]
    s = parse_schedule_json(lessons_json, school="X")
    slojd = next(lesson for lesson in s.lessons if lesson.subject == "Slöjd")
    assert slojd.attendance_status == "Närvarande"
    rast = next(lesson for lesson in s.lessons if lesson.subject == "Rast")
    assert rast.attendance_status == ""


def test_lesson_id_and_color(lessons_json: list[dict]) -> None:  # type: ignore[type-arg]
    s = parse_schedule_json(lessons_json, school="X")
    slojd = next(lesson for lesson in s.lessons if lesson.subject == "Slöjd")
    assert slojd.lesson_id == 5001
    assert slojd.color == "#b820e7"


def test_all_day_events_merged(
    lessons_json: list[dict],  # type: ignore[type-arg]
    events_json: list[dict],  # type: ignore[type-arg]
) -> None:
    s = parse_schedule_json(lessons_json, events_json, school="X")
    titles = [e.title for e in s.all_day_events]
    assert "Idrott åk5" in titles
    assert "Planering svenska" in titles
    sport = next(e for e in s.all_day_events if e.title == "Idrott åk5")
    assert sport.start_day == "2026-05-20"
    assert sport.end_day == "2026-05-20"


def test_no_events_payload_yields_empty_list(lessons_json: list[dict]) -> None:  # type: ignore[type-arg]
    s = parse_schedule_json(lessons_json, None, school="X")
    assert s.all_day_events == []


def test_empty_lessons_and_no_events_returns_note() -> None:
    s = parse_schedule_json([], [], school="X")
    assert s.lessons == []
    assert s.all_day_events == []
    assert s.note is not None


def test_missing_dates_drops_lesson() -> None:
    """A lesson with no startDate/endDate is dropped (parser is defensive)."""
    broken = [{"eventId": 1, "name": "Math"}]  # no start/end
    s = parse_schedule_json(broken, school="X")
    assert s.lessons == []


def test_non_list_payload_does_not_crash() -> None:
    s = parse_schedule_json({"unexpected": "shape"}, school="X")
    assert s.lessons == []
    assert s.all_day_events == []


def test_omitted_week_year_falls_back_to_today() -> None:
    """Caller may omit week/year; result must carry today's ISO values, not 0."""
    import datetime as dt

    s = parse_schedule_json([], school="X")
    iso_year, iso_week, _ = dt.date.today().isocalendar()
    assert s.week == iso_week
    assert s.year == iso_year
