"""Tests for the fritids (after-school care) parser.

Fixtures are hand-written from the live page's shape. Every value —
requestid, times, comments — is invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schoolsoft_mcp.parsers import fritids as fr

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def month_html() -> str:
    return (FIXTURES / "fritids_month.html").read_text(encoding="utf-8")


@pytest.fixture
def empty_html() -> str:
    return (FIXTURES / "fritids_empty.html").read_text(encoding="utf-8")


@pytest.fixture
def parsed(month_html: str) -> dict:
    return fr.parse_fritids(month_html, school="x")


# --- Month calendar ---------------------------------------------------------


class TestMonthCalendar:
    def test_every_day_cell_gets_a_date_from_its_link(self, parsed: dict) -> None:
        # Two week rows of seven. The date comes from ``fromdate=`` in the
        # edit link, never from the visible "31 Augusti", which only names
        # the month on the first cell and would need locale tables.
        days = parsed["days"]
        assert len(days) == 14
        assert days[0]["date"] == "2026-08-31"
        assert days[-1]["date"] == "2026-09-13"

    def test_no_cell_is_lost_to_its_css_class(self, parsed: dict) -> None:
        # The live page styles Sundays "sunday" and the current week
        # "mondayThisweek"/"middleThisweek"/"sundayThisweek". A parser that
        # recognised days by class dropped today, every Sunday and the whole
        # current week — eleven of thirty-five cells — while the fixture,
        # styled uniformly, kept passing. The fixture now carries the real
        # classes, so this asserts what the first version silently got wrong.
        dates = [d["date"] for d in parsed["days"]]
        assert "2026-09-06" in dates  # Sunday, class "sunday"
        assert "2026-09-09" in dates  # mid current week, "middleThisweek"
        assert "2026-09-13" in dates  # "sundayThisweek"
        assert len(dates) == len(set(dates)) == 14

    def test_the_week_number_cell_is_not_mistaken_for_a_day(self) -> None:
        # It links with a fromdate too, but names no day.
        html = """<div id="main"><div class="h2">Anmälda tider september 2026</div>
        <table class="monthback"><tr>
          <td class="weekback"><a href="x.jsp?action=edit&amp;requestid=900010&amp;week=37&amp;fromdate=2026-09-07&amp;todate=2026-09-13">37</a></td>
          <td class="mondayThisweekOff"><div class="date"><a href="x.jsp?action=edit&amp;requestid=900010&amp;week=37&amp;day=2&amp;fromdate=2026-09-28">28</a></div><br/><a href="x.jsp?day=2&amp;fromdate=2026-09-28">8:00 - 16:30</a></td>
          <td class="middleThisweek"><div class="date"><a href="x.jsp?action=edit&amp;requestid=900010&amp;week=37&amp;day=5&amp;fromdate=2026-10-01">1 Oktober</a></div><br/><a href="x.jsp?day=5&amp;fromdate=2026-10-01"></a></td>
        </tr></table></div>"""
        out = fr.parse_fritids(html, school="x")
        assert [d["date"] for d in out["days"]] == ["2026-09-28", "2026-10-01"]
        by = {d["date"]: d for d in out["days"]}
        assert by["2026-09-28"]["in_month"] is False  # "...ThisweekOff"
        assert by["2026-09-28"]["booked"] is True
        assert by["2026-10-01"]["in_month"] is True
        assert by["2026-10-01"]["booked"] is False

    def test_days_outside_the_month_are_marked(self, parsed: dict) -> None:
        by_date = {d["date"]: d for d in parsed["days"]}
        assert by_date["2026-08-31"]["in_month"] is False
        assert by_date["2026-09-01"]["in_month"] is True

    def test_booked_days_carry_both_times(self, parsed: dict) -> None:
        mon = {d["date"]: d for d in parsed["days"]}["2026-09-07"]
        assert mon["booked"] is True
        assert (mon["drop_off"], mon["pick_up"]) == ("8:00", "16:30")
        assert mon["weekday"] == "monday"
        assert mon["week"] == 37

    def test_an_unbooked_weekday_is_visible_as_such(self, parsed: dict) -> None:
        # Thursday 10 Sep has an edit link but no time. For a fritids child
        # that is a day nobody has arranged care for, and the family should
        # see it rather than have it blend in with the weekend.
        thu = {d["date"]: d for d in parsed["days"]}["2026-09-10"]
        assert thu["booked"] is False
        assert thu["drop_off"] == "" and thu["pick_up"] == ""

    def test_weekends_are_unbooked(self, parsed: dict) -> None:
        by_date = {d["date"]: d for d in parsed["days"]}
        assert by_date["2026-09-12"]["booked"] is False
        assert by_date["2026-09-13"]["weekday"] == "sunday"

    def test_dots_in_times_are_normalised(self, parsed: dict) -> None:
        # Guardians type "7.30" as often as "7:30" and the page keeps it.
        thu = {d["date"]: d for d in parsed["days"]}["2026-09-03"]
        assert (thu["drop_off"], thu["pick_up"]) == ("7:30", "15:00")

    def test_month_and_year_come_from_the_heading(self, parsed: dict) -> None:
        assert parsed["month"] == 9
        assert parsed["year"] == 2026
        assert parsed["month_label"] == "september 2026"


# --- Week detail --------------------------------------------------------------


class TestWeekDetail:
    def test_dates_follow_from_the_hidden_fromdate(self, parsed: dict) -> None:
        week = parsed["week_days"]
        assert parsed["week"] == 37
        assert [d["date"] for d in week] == [
            "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11",
        ]
        assert [d["weekday"] for d in week][:2] == ["monday", "tuesday"]

    def test_past_days_are_read_from_text(self, parsed: dict) -> None:
        mon = parsed["week_days"][0]
        assert (mon["drop_off"], mon["pick_up"]) == ("8:00", "16:30")
        assert mon["editable"] is False

    def test_future_days_are_read_from_input_values(self, parsed: dict) -> None:
        # Days still to come render as <input> fields; the booked time is in
        # their value attribute and the cell text is empty. Reading text
        # alone reported Friday as unbooked on the live page.
        fri = parsed["week_days"][4]
        assert (fri["drop_off"], fri["pick_up"]) == ("8:00", "16:30")
        assert fri["editable"] is True

    def test_an_editable_day_with_no_value_is_unbooked(self, parsed: dict) -> None:
        thu = parsed["week_days"][3]
        assert thu["editable"] is True
        assert thu["drop_off"] == "" and thu["pick_up"] == ""

    def test_school_hours_sit_beside_the_booked_times(self, parsed: dict) -> None:
        wed = parsed["week_days"][2]
        assert (wed["school_start"], wed["school_end"]) == ("8:30", "14:15")
        # Rendered as a link on future days; still just a time range.
        fri = parsed["week_days"][4]
        assert (fri["school_start"], fri["school_end"]) == ("8:30", "13:40")

    def test_comments_in_both_directions(self, parsed: dict) -> None:
        week = parsed["week_days"]
        assert week[2]["guardian_comment"] == "Hämtas av farfar"  # static text
        assert week[4]["guardian_comment"] == "Slutar 15:00"  # input value
        assert week[1]["staff_comment"] == "Ta med regnkläder"
        assert week[0]["staff_comment"] == ""

    def test_recurring_weeks_rule(self, parsed: dict) -> None:
        assert parsed["recurring_weeks"] == "37-51, 17-22"

    def test_opening_hours(self, parsed: dict) -> None:
        assert parsed["opening_hours"] == "6:30 - 17:30"


# --- Enrolment ------------------------------------------------------------------


class TestEnrolment:
    def test_a_booked_month_means_fritids(self, parsed: dict) -> None:
        assert parsed["has_fritids"] is True
        assert parsed["note"] is None

    def test_a_child_without_fritids_is_not_read_as_going_home_early(
        self, empty_html: str
    ) -> None:
        # The page renders for every child. Without this flag every empty
        # pick_up would read as "collected right after school".
        out = fr.parse_fritids(empty_html, school="x")
        assert out["days"] and all(not d["booked"] for d in out["days"])
        assert out["has_fritids"] is False
        assert out["note"] is not None and "not enrolled" in out["note"]

    def test_blank_opening_hours_stay_blank(self, empty_html: str) -> None:
        assert fr.parse_fritids(empty_html, school="x")["opening_hours"] == ""

    def test_no_calendar_at_all_points_at_dump_page(self) -> None:
        out = fr.parse_fritids("<html><body><div id='main'></div></body></html>", school="x")
        assert out["days"] == []
        assert out["note"] is not None and "dump_page" in out["note"]


# --- Navigation -----------------------------------------------------------------


class TestMonthQuery:
    def test_the_page_counts_months_from_zero(self) -> None:
        q = fr.month_query(2026, 9, 900010)
        assert q["month"] == "8"
        assert q["fromdate"] == "2026-09-01"
        assert q["requestid"] == "900010"

    def test_no_student_means_no_requestid(self) -> None:
        assert "requestid" not in fr.month_query(2026, 1, None)

    def test_an_impossible_month_is_refused(self) -> None:
        with pytest.raises(ValueError, match="month must be 1-12"):
            fr.month_query(2026, 13, None)


class TestClock:
    def test_normalisation(self) -> None:
        assert fr._clock(" 8.00 ") == "8:00"
        assert fr._clock("16:30") == "16:30"
        assert fr._clock("-") == ""
        assert fr._clock("") == ""
