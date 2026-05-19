"""Grades parser — ``right_student_gradesubject.jsp``.

The page renders a single table:

    Ämne | 25/26 Ht | 25/26 Vt | ... | Notering
    Bild | C        | ...      |     |
    ...

Header row identifies the term columns. Each data row is one subject
with one grade per term and an optional free-text note in the last
column. We emit one ``GradeEntry`` per non-empty (subject, term) pair so
callers can filter / group easily.
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup, Tag

from ..models import GradeEntry, GradeList

logger = logging.getLogger(__name__)

GRADES_PATHS = (
    "jsp/student/right_student_gradesubject.jsp",
)

# Header cells use a "<year>/<year>\nHt" layout (a <br/> in the markup).
# Anything that looks like "yy/yy Ht|Vt" we treat as a term column.
_TERM_RE = re.compile(r"^\d{2}/\d{2}\s+(?:ht|vt)\b", re.IGNORECASE)


def parse_grades(html: str, *, school: str) -> GradeList:
    soup = BeautifulSoup(html, "lxml")
    grades: list[GradeEntry] = []
    terms: list[str] = []

    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers = [c.get_text(" ", strip=True) for c in rows[0].find_all("td")]
        lower = [h.lower() for h in headers]
        # Only consider tables that look like the subject-grade table.
        if not any("ämne" in h or "amne" in h for h in lower):
            continue

        term_columns: list[tuple[int, str]] = []
        note_column: int | None = None
        for idx, header in enumerate(headers[1:], start=1):
            if _TERM_RE.match(header):
                term_columns.append((idx, header))
            elif "notering" in header.lower():
                note_column = idx

        terms = [t for _, t in term_columns]

        for row in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if not cells:
                continue
            subject = cells[0].strip()
            if not subject:
                continue
            row_note = (
                cells[note_column].strip()
                if note_column is not None and note_column < len(cells)
                else ""
            )
            for col_idx, term in term_columns:
                grade_value = (
                    cells[col_idx].strip() if col_idx < len(cells) else ""
                )
                if not grade_value and not row_note:
                    # Skip wholly-empty (subject, term) pairs to keep the
                    # response compact. Filed notes without a grade are
                    # still surfaced.
                    continue
                grades.append(
                    GradeEntry(
                        subject=subject,
                        term=term,
                        grade=grade_value,
                        note=row_note,
                    )
                )
        break  # Only the first matching table.

    note: str | None = None
    if not grades:
        note = (
            "No grades parsed. Either no grades have been reported yet, "
            "or the page layout differs from what's expected — call "
            "dump_page('jsp/student/right_student_gradesubject.jsp') and "
            "share a sanitised excerpt."
        )

    return GradeList(school=school, grades=grades, terms=terms, note=note)
