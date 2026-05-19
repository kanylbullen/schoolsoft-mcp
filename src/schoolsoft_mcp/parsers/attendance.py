"""Attendance parsers — weekly Rapport view + Oanmäld frånvaro list.

The previous version pointed at ``right_student_absence_overview.jsp``,
which returns 404 on at least some SchoolSoft installations. The actual
pages we want, from the sidebar:

- ``right_student_absence_student.jsp`` — Frånvaro → **Rapport**.
  Weekly stats table with counts + percentages per week.
- ``right_parent_absence_message.jsp`` — Frånvaro → **Oanmäld frånvaro**.
  List of unreported absence events (week / day / lesson / message).
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup, Tag

from ..models import (
    AttendanceReport,
    AttendanceWeek,
    UnreportedAbsenceEvent,
    UnreportedAbsenceList,
)

logger = logging.getLogger(__name__)

# Frånvaro → Rapport. First entry is the canonical path; the others are
# legacy fallbacks kept in case some SchoolSoft installations still use
# them.
ATTENDANCE_PATHS = (
    "jsp/student/right_student_absence_student.jsp",
    "jsp/student/right_student_absence_overview.jsp",
    "jsp/student/right_student_absence.jsp",
)

# Frånvaro → Oanmäld frånvaro.
UNREPORTED_ABSENCE_PATHS = (
    "jsp/student/right_parent_absence_message.jsp",
)

_WEEK_LABEL_RE = re.compile(r"^v\.?\s*(\d{1,3})$", re.IGNORECASE)
_COUNT_RE = re.compile(r"(\d+)\s*st", re.IGNORECASE)
_PERCENT_RE = re.compile(r"\(?([\d.,]+)\s*%\)?")

# Column index in the Rapport table → AttendanceWeek field.
# Column 0 is "Vecka" (handled separately).
# Column 4 in the source is a visual separator (empty header "&nbsp;&nbsp;").
_COUNTED_COLS = {
    1: "total_present",
    2: "unreported_absence",
    3: "reported_absence",
    5: "present",
    6: "present_other_assignment",
    7: "left_lesson",
    8: "present_preregistered",
    9: "late_arrival",
    10: "absent",
    11: "preregistered",
    12: "leave_granted",
}

# Subset of the columns above that print "<count> st (<percent>%)" — the
# rest print "<count> st" or just "-".
_PERCENTED_COLS = {1, 2, 3}


def parse_attendance(html: str, *, school: str) -> AttendanceReport:
    """Parse Frånvaro → Rapport into per-week aggregates."""
    soup = BeautifulSoup(html, "lxml")
    weeks: list[AttendanceWeek] = []

    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        # First row is the header.
        header_cells = [c.get_text(" ", strip=True) for c in rows[0].find_all("td")]
        if not any("vecka" in h.lower() for h in header_cells):
            continue
        for row in rows[1:]:
            week = _parse_attendance_row(row)
            if week is not None:
                weeks.append(week)
        break  # Only the first matching table; the page has just one.

    note: str | None = None
    if not weeks:
        note = (
            "Attendance parser found no weekly rows. Either there's nothing "
            "to report yet this term, or the page layout differs — call "
            "dump_page('jsp/student/right_student_absence_student.jsp') and "
            "share a sanitised excerpt."
        )

    return AttendanceReport(school=school, weeks=weeks, note=note)


def _parse_attendance_row(row: Tag) -> AttendanceWeek | None:
    cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
    if not cells:
        return None
    week_match = _WEEK_LABEL_RE.match(cells[0].strip().replace("\xa0", " "))
    if not week_match:
        return None
    fields: dict[str, int | float | None] = {"week": int(week_match.group(1))}
    for idx, attr in _COUNTED_COLS.items():
        if idx >= len(cells):
            continue
        text = cells[idx].replace("\xa0", " ")
        count_match = _COUNT_RE.search(text)
        fields[f"{attr}_count"] = int(count_match.group(1)) if count_match else 0
        if idx in _PERCENTED_COLS:
            pct_match = _PERCENT_RE.search(text)
            fields[f"{attr}_percent"] = (
                float(pct_match.group(1).replace(",", ".")) if pct_match else None
            )
    # Compact counts (no percent): some columns share the AttendanceWeek
    # field name directly without a _count suffix.
    flat = {
        "present": fields.pop("present_count", 0),
        "present_other_assignment": fields.pop("present_other_assignment_count", 0),
        "left_lesson": fields.pop("left_lesson_count", 0),
        "present_preregistered": fields.pop("present_preregistered_count", 0),
        "late_arrival": fields.pop("late_arrival_count", 0),
        "absent": fields.pop("absent_count", 0),
        "preregistered": fields.pop("preregistered_count", 0),
        "leave_granted": fields.pop("leave_granted_count", 0),
    }
    return AttendanceWeek(**fields, **flat)  # type: ignore[arg-type]


def parse_unreported_absence(html: str, *, school: str) -> UnreportedAbsenceList:
    """Parse Frånvaro → Oanmäld frånvaro into a flat list of events.

    The page uses ``&nbsp;`` in week and day cells to indicate "same as
    previous row" (visual continuation). We forward-fill week and day so
    every event has them populated.
    """
    soup = BeautifulSoup(html, "lxml")
    events: list[UnreportedAbsenceEvent] = []

    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_cells = [c.get_text(" ", strip=True) for c in rows[0].find_all("td")]
        header_lower = [h.lower() for h in header_cells]
        if not (
            any("vecka" in h for h in header_lower)
            and any("lektion" in h for h in header_lower)
        ):
            continue

        last_week: int | None = None
        last_day = ""
        for row in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if len(cells) < 3:
                continue
            week_str = cells[0].strip().replace("\xa0", " ")
            day = cells[1].strip().replace("\xa0", " ")
            lesson = cells[2].strip()
            message = cells[3].strip() if len(cells) > 3 else ""
            if not lesson:
                continue

            week_match = _WEEK_LABEL_RE.match(week_str)
            if week_match:
                last_week = int(week_match.group(1))
            if day:
                last_day = day
            if last_week is None:
                continue

            events.append(
                UnreportedAbsenceEvent(
                    week=last_week,
                    day=last_day,
                    lesson=lesson,
                    message=message,
                )
            )
        break

    note: str | None = None
    if not events:
        note = (
            "No unreported-absence entries found. Either none are outstanding "
            "or the page layout differs — call "
            "dump_page('jsp/student/right_parent_absence_message.jsp') and "
            "share a sanitised excerpt."
        )

    return UnreportedAbsenceList(school=school, events=events, note=note)
