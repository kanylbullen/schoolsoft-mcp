"""Schedule parser — EXPERIMENTAL.

SchoolSoft's schedule view is typically a JS-rendered image/canvas on the
classic JSP UI, which makes pure HTML scraping unreliable. This parser
attempts a best-effort table extraction and may need adjustment per school.

If `parse_schedule` returns an empty lesson list, use the `dump_page` MCP tool
to capture the raw HTML and open an issue with a sanitized snippet so the
selectors can be improved.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..models import WEEKDAYS, Lesson, ScheduleWeek

logger = logging.getLogger(__name__)

SCHEDULE_PATHS = (
    "jsp/student/right_student_schedule.jsp",
    "jsp/student/right_student_schedule_data.jsp",
)

_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def parse_schedule(
    html: str, *, school: str, requested_week: int | None = None
) -> ScheduleWeek:
    soup = BeautifulSoup(html, "lxml")
    now = datetime.now()
    week = requested_week if requested_week is not None else now.isocalendar()[1]
    year = now.year

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
                Lesson(
                    day=day,
                    start=start,
                    end=end,
                    subject=subject,
                    teacher="",
                    room="",
                )
            )

    note: str | None = None
    if not lessons:
        note = (
            "Schedule parser found no lessons. SchoolSoft renders the schedule "
            "via JavaScript on most schools, so HTML scraping is unreliable. "
            "Use the dump_page tool to inspect the raw response."
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
