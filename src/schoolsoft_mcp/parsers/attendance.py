"""Attendance / frånvaro parser — EXPERIMENTAL."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import AttendanceEntry, AttendanceReport

logger = logging.getLogger(__name__)

ATTENDANCE_PATHS = (
    "jsp/student/right_student_absence_overview.jsp",
    "jsp/student/right_student_absence.jsp",
)

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_MINUTES_RE = re.compile(r"\b(\d{1,3})\s*min\b", re.IGNORECASE)


def parse_attendance(html: str, *, school: str) -> AttendanceReport:
    soup = BeautifulSoup(html, "lxml")
    entries: list[AttendanceEntry] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if len(cells) < 2:
                continue
            joined = " | ".join(cells)
            date_match = _DATE_RE.search(joined)
            if not date_match:
                continue
            minutes_match = _MINUTES_RE.search(joined)
            entries.append(
                AttendanceEntry(
                    date=date_match.group(1),
                    subject=_first_non_date(cells),
                    minutes=int(minutes_match.group(1)) if minutes_match else None,
                    status=_status_from(cells),
                    reason=cells[-1] if cells else "",
                )
            )

    note: str | None = None
    if not entries:
        note = (
            "Attendance parser found no entries. Either there's nothing to report "
            "or the page structure differs from what's expected — use dump_page "
            "to inspect."
        )

    return AttendanceReport(school=school, entries=entries, note=note)


def _first_non_date(cells: list[str]) -> str:
    for c in cells:
        if c and not _DATE_RE.search(c):
            return c
    return ""


def _status_from(cells: list[str]) -> str:
    keywords = ("anmäld", "ogiltig", "giltig", "sen", "frånvaro", "närvaro")
    for c in cells:
        lowered = c.lower()
        if any(k in lowered for k in keywords):
            return c
    return ""
