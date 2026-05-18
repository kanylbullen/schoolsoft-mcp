"""Homework / assignments parser — EXPERIMENTAL."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup, Tag

from ..models import HomeworkItem, HomeworkList

logger = logging.getLogger(__name__)

HOMEWORK_PATHS = (
    "jsp/student/right_student_homework.jsp",
    "jsp/student/right_student_assignment.jsp",
)

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def parse_homework(html: str, *, school: str) -> HomeworkList:
    soup = BeautifulSoup(html, "lxml")
    items: list[HomeworkItem] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            texts = [c.get_text(" ", strip=True) for c in cells]
            full = " | ".join(t for t in texts if t)
            if not full or not any(_looks_substantive(t) for t in texts):
                continue
            items.append(
                HomeworkItem(
                    subject=_first_short(texts),
                    title=_longest(texts),
                    description=full,
                    due=_find_date(full),
                    assigned=None,
                )
            )

    note: str | None = None
    if not items:
        note = (
            "Homework parser found no entries. The page structure varies by "
            "school; use dump_page to inspect the raw HTML."
        )
        logger.warning(note)

    return HomeworkList(school=school, items=items, note=note)


def _looks_substantive(text: str) -> bool:
    return len(text) > 4 and any(c.isalpha() for c in text)


def _first_short(texts: list[str]) -> str:
    for t in texts:
        if 0 < len(t) <= 40:
            return t
    return ""


def _longest(texts: list[str]) -> str:
    return max(texts, key=len, default="")


def _find_date(text: str) -> str | None:
    m = _DATE_RE.search(text)
    return m.group(1) if m else None


def _row_cells(row: Tag) -> list[str]:
    return [c.get_text(" ", strip=True) for c in row.find_all("td")]
