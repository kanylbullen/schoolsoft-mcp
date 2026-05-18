from pathlib import Path

import pytest

from schoolsoft_mcp.parsers.lunch import format_meal_text, parse_lunch

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def lunch_html() -> str:
    return (FIXTURES / "lunch_simple.html").read_text(encoding="utf-8")


@pytest.fixture
def lunch_empty_html() -> str:
    return (FIXTURES / "lunch_empty.html").read_text(encoding="utf-8")


class TestFormatMealText:
    def test_splits_on_vegetariskt(self) -> None:
        out = format_meal_text("Köttfärssås Vegetariskt Sojafärssås")
        assert out == "Köttfärssås | Veg: Sojafärssås"

    def test_handles_colon_variant(self) -> None:
        out = format_meal_text("Fiskgryta Vegetariskt: Linsgryta")
        assert out == "Fiskgryta | Veg: Linsgryta"

    def test_no_vegetarian_alternative(self) -> None:
        out = format_meal_text("Pizza")
        assert out == "Pizza"

    def test_only_vegetarian(self) -> None:
        out = format_meal_text("Vegetariskt: Linsgryta")
        assert out == "Veg: Linsgryta"

    def test_strips_surrounding_whitespace(self) -> None:
        assert format_meal_text("   Soppa   ") == "Soppa"


class TestParseLunch:
    def test_returns_all_five_weekdays(self, lunch_html: str) -> None:
        week = parse_lunch(lunch_html, school="yourschool", requested_week=12)
        assert [d.day for d in week.days] == [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
        ]
        assert week.week == 12
        assert week.school == "yourschool"

    def test_parses_meals(self, lunch_html: str) -> None:
        week = parse_lunch(lunch_html, school="yourschool", requested_week=12)
        by_day = {d.day: d.meal for d in week.days}
        assert by_day["monday"].startswith("Köttfärssås")
        assert "Veg:" in by_day["monday"]
        assert by_day["wednesday"] == "Kycklingsoppa"
        assert by_day["friday"] == "Pizza"

    def test_empty_page_returns_all_blank_days(self, lunch_empty_html: str) -> None:
        week = parse_lunch(lunch_empty_html, school="yourschool", requested_week=1)
        assert all(d.meal == "" for d in week.days)
        assert len(week.days) == 5

    def test_uses_current_week_when_unspecified(self, lunch_html: str) -> None:
        week = parse_lunch(lunch_html, school="yourschool")
        assert 1 <= week.week <= 53

    def test_as_text_contains_school_and_week(self, lunch_html: str) -> None:
        week = parse_lunch(lunch_html, school="yourschool", requested_week=42)
        text = week.as_text()
        assert "yourschool" in text
        assert "42" in text
        assert "Monday" in text
