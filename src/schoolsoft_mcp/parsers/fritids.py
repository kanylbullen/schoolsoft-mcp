"""Fritids — the after-school care times a guardian has booked.

``right_parent_preschool_schedule_new.jsp`` ("Mina tider") is the one page
on the parent surface that changes what a family does *every single day*
for a younger child: when the child is dropped off and, above all, when
somebody has to be at the school to collect them. It only appears in the
menu for children enrolled in fritids, which is why nothing else here knew
about it.

The page renders two things:

- A **month calendar** (``table.monthback``). One row per week, one cell per
  day. A booked day carries a time range ("8:00 - 16:30"); an unbooked day
  carries nothing. The exact date is in each cell's edit link as
  ``fromdate=YYYY-MM-DD`` together with ``day=``, which is read instead of
  the visible "31 Augusti", because the visible form only names the month
  on the first day shown and would need locale tables to turn back into a
  date. Cells are recognised by that link, not by CSS class — the page uses
  a different class for Sundays, for the current week, and for days of the
  adjacent month, and a class allowlist dropped a third of the month.
- A **week detail** (``table.table-condensed``) for the week containing
  ``fromdate``: drop-off/pick-up per weekday, the school day next to it,
  the recurring-weeks rule, and comments in both directions. Days already
  past are static text; days still to come are ``<input>`` fields whose
  ``value`` holds the time, so both must be read.

SchoolSoft numbers weekdays with **Sunday as 1**: the inputs are named
``starttime_5`` for Thursday and the edit links say ``day=2`` for Monday.

Navigation is ``?requestid=<student_id>&month=<0-based>&year=<yyyy>
&fromdate=<yyyy-mm-dd>``. ``month`` is zero-based — September is 8.

Read only. The form on the page updates times with ``action=update``; this
module never sends it. Booking a child's care hours is the guardian's act.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from ._fields import ISO_DATE_RE

FRITIDS_PATH = "jsp/student/right_parent_preschool_schedule_new.jsp"

# SchoolSoft's weekday index: Sunday first.
SS_DAY_INDEX: dict[int, str] = {
    1: "sunday",
    2: "monday",
    3: "tuesday",
    4: "wednesday",
    5: "thursday",
    6: "friday",
    7: "saturday",
}
_WEEKDAY_BY_ISO: dict[int, str] = {
    1: "monday",
    2: "tuesday",
    3: "wednesday",
    4: "thursday",
    5: "friday",
    6: "saturday",
    7: "sunday",
}
_SV_MONTHS: dict[str, int] = {
    "januari": 1, "februari": 2, "mars": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "augusti": 8, "september": 9, "oktober": 10, "november": 11,
    "december": 12,
}

_TIME_RANGE = re.compile(r"(\d{1,2}[:.]\d{2})\s*-\s*(\d{1,2}[:.]\d{2})")
_FROMDATE = re.compile(r"fromdate=(\d{4}-\d{2}-\d{2})")
# A day cell's edit link names the day; the week cell's link does not.
_DAY_LINK = re.compile(r"[?&]day=\d")
_MONTH_HEADING = re.compile(
    r"Anmälda tider\s+([a-zåäö]+)\s+(\d{4})", re.IGNORECASE
)
_OPENING = re.compile(
    r"Öppettider för hämtning/lämning:\s*(.*?)(?:Ange tid|$)"
)
_INPUT_DAY = re.compile(r"_(\d)$")


def _clock(value: str) -> str:
    """``"8.00"`` / ``" 8:00 "`` -> ``"8:00"``; empty stays empty."""
    value = value.strip().replace(".", ":")
    return value if re.fullmatch(r"\d{1,2}:\d{2}", value) else ""


def _range_from_text(text: str) -> tuple[str, str]:
    match = _TIME_RANGE.search(text)
    if not match:
        return "", ""
    return _clock(match.group(1)), _clock(match.group(2))


def _cell_times(cell: Tag) -> tuple[str, str, bool]:
    """(start, end, editable) for a week-detail cell.

    A day that has passed is plain text. A day still to come is a pair of
    inputs, and the value a guardian booked sits in their ``value``
    attributes rather than in the text.
    """
    inputs = cell.find_all("input", attrs={"type": "text"})
    if inputs:
        values = [_clock(str(i.get("value") or "")) for i in inputs]
        start = values[0] if values else ""
        end = values[1] if len(values) > 1 else ""
        return start, end, True
    start, end = _range_from_text(cell.get_text(" ", strip=True))
    return start, end, False


def _cell_text_or_input(cell: Tag) -> str:
    inp = cell.find("input", attrs={"type": "text"})
    if isinstance(inp, Tag):
        return str(inp.get("value") or "").strip()
    return cell.get_text(" ", strip=True)


def _day_cell_date(cell: Tag) -> str:
    """The ISO date of a day cell, or "" if the cell is not a day.

    Identified by the edit link naming a ``day=`` — the week-number cell
    also links with a ``fromdate`` but has no day. Deliberately not by CSS
    class: the live page styles ordinary days ``monthon``/``monthoff``,
    Sundays ``sunday``, and the current week ``mondayThisweek``,
    ``middleThisweek``, ``sundayThisweek`` and ``…ThisweekOff``. A class
    allowlist silently dropped today, every Sunday, and the whole current
    week — eleven of thirty-five cells — while the tests passed.
    """
    for a in cell.find_all("a", href=True):
        href = str(a["href"])
        match = _FROMDATE.search(href)
        if match and _DAY_LINK.search(href):
            return match.group(1)
    return ""


def parse_month(table: Tag) -> list[dict[str, Any]]:
    """The month calendar: one dict per day cell, dated from its edit link."""
    days: list[dict[str, Any]] = []
    for row in table.find_all("tr"):
        week_cell = row.find("td", class_="weekback")
        if not isinstance(week_cell, Tag):
            continue
        week_text = week_cell.get_text(" ", strip=True)
        week = int(week_text) if week_text.isdigit() else None
        for cell in row.find_all("td"):
            if not isinstance(cell, Tag):
                continue
            date = _day_cell_date(cell)
            if not date:
                continue
            start, end = _range_from_text(cell.get_text(" ", strip=True))
            iso = dt.date.fromisoformat(date)
            classes = " ".join(cell.get("class") or []).lower()
            days.append(
                {
                    "date": date,
                    "weekday": _WEEKDAY_BY_ISO[iso.isoweekday()],
                    "week": week,
                    "drop_off": start,
                    "pick_up": end,
                    "booked": bool(start and end),
                    # "monthoff", "mondayThisweekOff": outside the shown month.
                    "in_month": "off" not in classes,
                }
            )
    return days


def parse_week_detail(
    table: Tag, *, week_start: dt.date | None
) -> tuple[list[dict[str, Any]], str]:
    """The week block under the calendar.

    Returns ``(days, recurring_weeks)``. ``days`` has one entry per weekday
    column; ``recurring_weeks`` is the "Ändra tid för veckor" rule, e.g.
    ``"37-51, 17-22"`` — the weeks the booked times repeat over.
    """
    rows = table.find_all("tr")
    if not rows:
        return [], ""
    header = [c.get_text(" ", strip=True) for c in rows[0].find_all("td")]
    # Column 0 is the row label; the rest are weekdays in order.
    columns = max(len(header) - 1, 0)
    days: list[dict[str, Any]] = [
        {
            "date": (
                (week_start + dt.timedelta(days=i)).isoformat() if week_start else ""
            ),
            "weekday": _WEEKDAY_BY_ISO[i + 1] if i < 7 else "",
            "drop_off": "",
            "pick_up": "",
            "school_start": "",
            "school_end": "",
            "guardian_comment": "",
            "staff_comment": "",
            "editable": False,
        }
        for i in range(columns)
    ]
    recurring = ""
    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True).lower()
        body = cells[1 : 1 + columns]
        if label.startswith("lämnas"):
            for i, cell in enumerate(body):
                start, end, editable = _cell_times(cell)
                days[i].update(drop_off=start, pick_up=end, editable=editable)
        elif label.startswith("skoltider"):
            for i, cell in enumerate(body):
                start, end = _range_from_text(cell.get_text(" ", strip=True))
                days[i].update(school_start=start, school_end=end)
        elif label.startswith("kommentar från vårdnadshavare"):
            for i, cell in enumerate(body):
                days[i]["guardian_comment"] = _cell_text_or_input(cell)
        elif label.startswith("kommentar från personal"):
            for i, cell in enumerate(body):
                days[i]["staff_comment"] = cell.get_text(" ", strip=True)
        elif label.startswith("ändra tid"):
            for cell in body:
                inp = cell.find("input", attrs={"type": "text"})
                if isinstance(inp, Tag) and str(inp.get("value") or "").strip():
                    recurring = str(inp.get("value")).strip()
                    break
    return days, recurring


def parse_fritids(html: str, *, school: str) -> dict[str, Any]:
    """Parse the whole page into the shape ``FritidsTimes`` takes."""
    soup = BeautifulSoup(html, "lxml")
    root = soup.find(id="main")
    root = root if isinstance(root, Tag) else soup

    text = root.get_text(" ", strip=True)
    heading = _MONTH_HEADING.search(text)
    month = _SV_MONTHS.get(heading.group(1).lower(), 0) if heading else 0
    year = int(heading.group(2)) if heading else 0
    month_label = f"{heading.group(1).lower()} {heading.group(2)}" if heading else ""

    month_table = root.find("table", class_="monthback")
    days = parse_month(month_table) if isinstance(month_table, Tag) else []

    hidden = {
        str(i.get("name")): str(i.get("value") or "")
        for i in root.find_all("input", attrs={"type": "hidden"})
        if i.get("name")
    }
    week_from = hidden.get("fromdate", "")
    week_start = (
        dt.date.fromisoformat(week_from) if ISO_DATE_RE.fullmatch(week_from) else None
    )
    week_no = int(hidden["week"]) if hidden.get("week", "").isdigit() else None

    detail_table = root.find("table", class_="table-condensed")
    week_days, recurring = (
        parse_week_detail(detail_table, week_start=week_start)
        if isinstance(detail_table, Tag)
        else ([], "")
    )

    opening = _OPENING.search(text)
    opening_hours = opening.group(1).strip(" -") if opening else ""

    booked = [d for d in days if d["booked"]]
    has_fritids = bool(booked) or any(d["drop_off"] for d in week_days)

    if not days:
        note: str | None = (
            "No calendar found on the page. Either the child is not enrolled "
            "in fritids, or the layout differs — call "
            f"dump_page('{FRITIDS_PATH}') and share a sanitised excerpt."
        )
    elif not has_fritids:
        note = (
            "The page rendered but no day has a booked time. This child is "
            "not enrolled in fritids, or nothing is booked this month."
        )
    else:
        note = None

    return {
        "school": school,
        "year": year,
        "month": month,
        "month_label": month_label,
        "days": days,
        "week": week_no,
        "week_days": week_days,
        "recurring_weeks": recurring,
        "opening_hours": opening_hours,
        "has_fritids": has_fritids,
        "note": note,
    }


def month_query(year: int, month: int, student_id: int | None) -> dict[str, str]:
    """Query string for a given month. ``month`` is 1-12 here; the page wants 0-11."""
    if not 1 <= month <= 12:
        raise ValueError(f"month must be 1-12, got {month}")
    params = {
        "month": str(month - 1),
        "year": str(year),
        "fromdate": dt.date(year, month, 1).isoformat(),
    }
    if student_id is not None:
        params["requestid"] = str(student_id)
    return params
