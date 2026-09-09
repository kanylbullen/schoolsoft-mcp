"""Schedule parser — REST first, JSP fallback.

Modern path:
    rest-api/parent/calendar/lessons/week/{week}
Returns a JSON array of lesson events for the active child's week. Each
item has eventId, name/description, startDate/endDate (ISO 8601),
allDay, room, teachingGroup, teacher, dayId (0=Monday … 4=Friday),
status, an optional studentLessonStatus block with the per-student
attendance state, and eventColor.

All-day events (sport days, "Planering svenska (fylls på kontinuerligt)")
come from a sibling endpoint:
    rest-api/parent/calendar/event/year/{year}/week/{week}
We merge those into ``ScheduleWeek.all_day_events``.

The legacy JSP scraper is kept as a fallback for installations that
haven't migrated.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from ..models import WEEKDAYS, AllDayEvent, Lesson, ScheduleWeek
from ._fields import int_field, str_field

logger = logging.getLogger(__name__)

SCHEDULE_PATHS = (
    "jsp/student/right_student_schedule.jsp",
    "jsp/student/right_student_schedule_data.jsp",
)
SCHEDULE_REST_LESSONS_PATH_TEMPLATE = "rest-api/parent/calendar/lessons/week/{week}"
SCHEDULE_REST_EVENTS_PATH_TEMPLATE = (
    "rest-api/parent/calendar/event/year/{year}/week/{week}"
)

_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")

# Subject names that should be flagged is_break=True. SchoolSoft uses the
# same lesson record type for non-academic slots, just with these labels.
_BREAK_NAMES = {"rast", "lunch", "lunchvakt", "promenad", "läxläsn.", "läxläsning"}


def parse_schedule_json(
    lessons_payload: Any,
    events_payload: Any = None,
    *,
    school: str,
    week: int | None = None,
    year: int | None = None,
    student_id: int | None = None,
) -> ScheduleWeek:
    """Parse the JSON returned by ``rest-api/parent/calendar/lessons/week/<N>``.

    ``events_payload`` is the optional response from the sibling all-day
    events endpoint; pass it when you've fetched it, or leave ``None`` to
    skip the merge. When ``week``/``year`` aren't provided we fall back
    to today's ISO values so the response metadata is always meaningful
    (never 0).
    """
    iso_year, iso_week, _ = datetime.now().isocalendar()
    resolved_week = week if week is not None else iso_week
    resolved_year = year if year is not None else iso_year

    lessons: list[Lesson] = []
    if isinstance(lessons_payload, list):
        for entry in lessons_payload:
            if not isinstance(entry, dict):
                continue
            parsed = _lesson_from_entry(entry)
            if parsed is not None:
                lessons.append(parsed)

    all_day = _parse_all_day_events(events_payload)

    note: str | None = None
    if not lessons and not all_day:
        note = (
            "No schedule entries for the requested week. The active child "
            "may not have a schedule yet, or the API shape changed — call "
            "dump_json on rest-api/parent/calendar/lessons/week/<N> to "
            "inspect."
        )

    return ScheduleWeek(
        week=resolved_week,
        year=resolved_year,
        school=school,
        student_id=student_id,
        lessons=lessons,
        all_day_events=all_day,
        note=note,
    )


def _lesson_from_entry(entry: dict[str, Any]) -> Lesson | None:
    start = _parse_dt(entry.get("startDate"))
    end = _parse_dt(entry.get("endDate"))
    if start is None or end is None:
        return None
    day_id = entry.get("dayId")
    if isinstance(day_id, int) and 0 <= day_id < len(WEEKDAYS):
        day = WEEKDAYS[day_id]
    else:
        # Fall back to deriving from startDate's weekday() — Mon=0..Sun=6.
        weekday = start.weekday()
        day = WEEKDAYS[weekday] if 0 <= weekday < len(WEEKDAYS) else "unknown"

    name = _str_field(entry, "name")
    description = _str_field(entry, "description")
    notes = description if description and description != name else ""
    status_block = entry.get("studentLessonStatus")
    attendance_status = ""
    if isinstance(status_block, dict):
        attendance_status = _str_field(status_block, "name")

    return Lesson(
        day=day,
        start=start.strftime("%H:%M"),
        end=end.strftime("%H:%M"),
        subject=name,
        teacher=_str_field(entry, "teacher"),
        room=_str_field(entry, "room"),
        notes=notes,
        is_break=name.lower() in _BREAK_NAMES,
        lesson_id=_int_field(entry, "eventId"),
        teaching_group=_str_field(entry, "teachingGroup"),
        color=_str_field(entry, "eventColor"),
        attendance_status=attendance_status,
    )


def _parse_all_day_events(payload: Any) -> list[AllDayEvent]:
    """Parse the events sibling endpoint into AllDayEvent[].

    Shape is not yet documented (the sample was an empty list). We assume
    each entry has at least name/description/startDate/endDate; missing
    fields degrade to empty strings rather than dropping the event.
    """
    if not isinstance(payload, list):
        return []
    out: list[AllDayEvent] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        title = _str_field(entry, "name") or _str_field(entry, "title")
        if not title:
            continue
        out.append(
            AllDayEvent(
                title=title,
                start_day=_iso_date_field(entry, "startDate"),
                end_day=_iso_date_field(entry, "endDate"),
                description=_str_field(entry, "description"),
            )
        )
    return out


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        # SchoolSoft uses "YYYY-MM-DDTHH:MM" (no timezone). Tolerate stray
        # "Z" or "+00:00" suffixes just in case.
        cleaned = value.replace("Z", "").rstrip()
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _iso_date_field(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key)
    if isinstance(value, str) and value:
        # "2026-05-18T..." → "2026-05-18".
        return value.split("T", 1)[0]
    return ""


_str_field = str_field
_int_field = int_field


# ----------------------------------------------------------------------------
# Legacy JSP fallback (kept for installations without the REST surface).
# ----------------------------------------------------------------------------


def parse_schedule(
    html: str,
    *,
    school: str,
    requested_week: int | None = None,
    requested_year: int | None = None,
) -> ScheduleWeek:
    """Best-effort HTML scrape — the JSP schedule is JS-rendered on most
    schools, so this is unreliable. See ``parse_schedule_json`` for the
    modern path.
    """
    soup = BeautifulSoup(html, "lxml")
    iso_year, iso_week, _ = datetime.now().isocalendar()
    week = requested_week if requested_week is not None else iso_week
    year = requested_year if requested_year is not None else iso_year

    lessons: list[Lesson] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue
            joined = " ".join(c.get_text(" ", strip=True) for c in cells)
            times = _TIME_RE.findall(joined)
            if len(times) < 2:
                continue
            start = f"{int(times[0][0]):02d}:{times[0][1]}"
            end = f"{int(times[1][0]):02d}:{times[1][1]}"

            day = _guess_day(joined) or "unknown"
            texts = [c.get_text(" ", strip=True) for c in cells if c.get_text(strip=True)]
            subject = next(
                (t for t in texts if not _TIME_RE.search(t) and len(t) < 80),
                "",
            )
            lessons.append(
                Lesson(day=day, start=start, end=end, subject=subject)
            )

    note: str | None = None
    if not lessons:
        note = (
            "Schedule parser found no lessons in the legacy JSP. Most schools "
            "render the schedule via JavaScript; use get_schedule (REST) "
            "instead."
        )
        logger.warning(note)

    return ScheduleWeek(week=week, year=year, school=school, lessons=lessons, note=note)


def _guess_day(text: str) -> str | None:
    lowered = text.lower()
    swedish = {
        "måndag": "monday",
        "tisdag": "tuesday",
        "onsdag": "wednesday",
        "torsdag": "thursday",
        "fredag": "friday",
    }
    for sv, en in swedish.items():
        if sv in lowered:
            return en
    for en in WEEKDAYS:
        if en in lowered:
            return en
    return None
