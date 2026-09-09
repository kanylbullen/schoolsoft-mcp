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
# Trailing "2026-09-09 7:11" in the "Tagit del av" cell.
_ACK_STAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2}(?:\s+\d{1,2}[:.]\d{2})?)\s*$")
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


# Header text -> field, so the two layouts (the "still to acknowledge" table
# and the wider "already acknowledged" one) are read by name rather than by
# position. Position matching is what made the second look like the first.
_ABSENCE_COLS: tuple[tuple[str, str], ...] = (
    ("vecka", "week"),
    ("dag", "day"),
    ("lektion", "lesson"),
    ("meddelande", "message"),
    ("tagit del av", "acknowledged"),
    ("bekr\u00e4ftad av skolan", "school_confirmed"),
)

# The banner the page shows when nothing is outstanding.
_NONE_PENDING = "ingen oanm\u00e4ld fr\u00e5nvaro att ta del av"


def _absence_columns(header_cells: list[str]) -> dict[str, int] | None:
    """Map a header row to ``{field: index}``, or None if it is not the table."""
    found: dict[str, int] = {}
    for index, cell in enumerate(header_cells):
        text = cell.strip().lower()
        for needle, field in _ABSENCE_COLS:
            if needle in text:
                found.setdefault(field, index)
    if "week" in found and "lesson" in found:
        return found
    return None


def _cell(cells: list[Tag], cols: dict[str, int], field: str) -> str:
    index = cols.get(field)
    if index is None or index >= len(cells):
        return ""
    return cells[index].get_text(" ", strip=True).replace("\xa0", " ").strip()


def parse_unreported_absence(html: str, *, school: str) -> UnreportedAbsenceList:
    """Parse Fr\u00e5nvaro -> Oanm\u00e4ld fr\u00e5nvaro, split by acknowledgement.

    SchoolSoft texts a guardian when a lesson is missed and then expects
    them to open this page and confirm they have seen it. Confirmed rows
    stay on the page permanently, in a second table with the same columns
    plus "Tagit del av" — so reading the first recognisable table reports
    absences from weeks ago as outstanding, which is a false alarm every
    morning until somebody stops believing the alarm.

    A row counts as acknowledged when its "Tagit del av" cell has content.
    That is a property of the row, so it holds whichever table it came from
    and in whatever order the tables appear.

    The page uses ``&nbsp;`` in week and day cells to indicate "same as
    previous row" (visual continuation). We forward-fill week and day so
    every event has them populated.
    """
    soup = BeautifulSoup(html, "lxml")
    pending: list[UnreportedAbsenceEvent] = []
    acknowledged: list[UnreportedAbsenceEvent] = []

    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_cells = [c.get_text(" ", strip=True) for c in rows[0].find_all("td")]
        cols = _absence_columns(header_cells)
        if cols is None:
            continue

        last_week: int | None = None
        last_day = ""
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            lesson = _cell(cells, cols, "lesson")
            if not lesson:
                continue

            week_match = _WEEK_LABEL_RE.match(_cell(cells, cols, "week"))
            if week_match:
                last_week = int(week_match.group(1))
            day = _cell(cells, cols, "day")
            if day:
                last_day = day
            if last_week is None:
                continue

            # "Johan Larsson 2026-09-09 7:11" — the page puts the timestamp
            # after a <br>, so it arrives glued to the name.
            ack = _cell(cells, cols, "acknowledged")
            ack_at = ""
            if ack:
                stamp = _ACK_STAMP_RE.search(ack)
                if stamp:
                    ack_at = stamp.group(1)
                    ack = ack[: stamp.start()].strip()

            event = UnreportedAbsenceEvent(
                week=last_week,
                day=last_day,
                lesson=lesson,
                message=_cell(cells, cols, "message"),
                acknowledged_by=ack,
                acknowledged_at=ack_at,
                school_confirmed=_cell(cells, cols, "school_confirmed"),
            )
            (acknowledged if ack else pending).append(event)

    confirmed_none = _NONE_PENDING in soup.get_text(" ", strip=True).lower()

    note: str | None = None
    if pending:
        note = (
            f"{len(pending)} unreported absence(s) still awaiting a guardian's "
            "acknowledgement in SchoolSoft (Fr\u00e5nvaro -> Oanm\u00e4ld fr\u00e5nvaro). "
            "A guardian has to give that confirmation themselves; this server "
            "does not send it."
        )
    elif confirmed_none:
        note = "The page states there is no unreported absence to acknowledge."
        if acknowledged:
            note += (
                f" {len(acknowledged)} earlier one(s) remain listed as already "
                "acknowledged; that list is history, not outstanding work."
            )
    elif not acknowledged:
        note = (
            "No unreported-absence entries found, and the page did not state "
            "that there are none. Either the layout differs or the section was "
            "not rendered — call "
            "dump_page('jsp/student/right_parent_absence_message.jsp') and "
            "share a sanitised excerpt."
        )

    return UnreportedAbsenceList(
        school=school,
        events=pending,
        acknowledged=acknowledged,
        confirmed_none_pending=confirmed_none,
        note=note,
    )
