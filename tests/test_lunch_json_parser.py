"""Tests for the REST lunch-menu parser (parse_lunch_json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from schoolsoft_mcp.parsers.lunch import parse_lunch_json

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def lunch_json() -> list[dict]:  # type: ignore[type-arg]
    return json.loads((FIXTURES / "lunchmenu_week.json").read_text(encoding="utf-8"))


def test_parses_all_five_weekdays(lunch_json: list[dict]) -> None:  # type: ignore[type-arg]
    week = parse_lunch_json(lunch_json, school="X", requested_week=21)
    days = [d.day for d in week.days]
    assert days == ["monday", "tuesday", "wednesday", "thursday", "friday"]
    assert week.week == 21


def test_main_and_vegetarian_combined(lunch_json: list[dict]) -> None:  # type: ignore[type-arg]
    """The REST payload concatenates main + Vegetarisk[t] + veg with \\r\\n separators."""
    week = parse_lunch_json(lunch_json, school="X")
    monday = next(d for d in week.days if d.day == "monday")
    assert monday.meal == (
        "Chili con carne serveras med ris | Veg: Chili sine carne serveras med ris"
    )


def test_vegetariskt_spelling_variant(lunch_json: list[dict]) -> None:  # type: ignore[type-arg]
    """``Vegetarisk`` and ``Vegetariskt`` both work as the marker."""
    week = parse_lunch_json(lunch_json, school="X")
    tuesday = next(d for d in week.days if d.day == "tuesday")
    assert "Pannkakor med sylt och grädde" in tuesday.meal
    assert "Veg: Pannkakor med sylt och grädde" in tuesday.meal


def test_empty_dishes_day_has_no_meal(lunch_json: list[dict]) -> None:  # type: ignore[type-arg]
    """Day with no dishes (e.g. school holiday) yields empty meal."""
    week = parse_lunch_json(lunch_json, school="X")
    wednesday = next(d for d in week.days if d.day == "wednesday")
    assert wednesday.meal == ""


def test_main_only_when_no_vegetarian(lunch_json: list[dict]) -> None:  # type: ignore[type-arg]
    """When the marker isn't present, the whole text becomes the main."""
    week = parse_lunch_json(lunch_json, school="X")
    thursday = next(d for d in week.days if d.day == "thursday")
    assert thursday.meal == "Endast huvudrätt utan vegoalternativ"


def test_uses_current_iso_year_when_no_request() -> None:
    """Without a ``requested_week``, fall back to today's ISO week."""
    import datetime as dt

    week = parse_lunch_json([], school="X")
    iso_year, iso_week, _ = dt.date.today().isocalendar()
    assert week.year == iso_year
    assert week.week == iso_week


def test_empty_payload_returns_skeleton_week() -> None:
    """An empty list still yields all five days (with empty meals)."""
    week = parse_lunch_json([], school="X", requested_week=10)
    assert week.week == 10
    assert len(week.days) == 5
    assert all(d.meal == "" for d in week.days)


def test_non_list_payload_does_not_crash() -> None:
    """Defensive: if SchoolSoft ever changes the shape, return an empty week."""
    week = parse_lunch_json({"unexpected": "shape"}, school="X", requested_week=12)
    assert week.week == 12
    assert all(d.meal == "" for d in week.days)


def test_school_propagated(lunch_json: list[dict]) -> None:  # type: ignore[type-arg]
    week = parse_lunch_json(lunch_json, school="yourschool")
    assert week.school == "yourschool"
