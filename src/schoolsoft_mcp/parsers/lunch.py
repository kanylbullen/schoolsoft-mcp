"""Lunch menu parser — ported from the Home Assistant integration."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from ..models import WEEKDAYS, LunchDay, LunchWeek

logger = logging.getLogger(__name__)

LUNCH_PATH = "jsp/student/right_student_lunchmenu.jsp"

_VEG_SPLIT = re.compile(r"(?:^|\s+)Vegetariskt\s*:?\s*", re.IGNORECASE)


def format_meal_text(text: str) -> str:
    """Split 'Vegetariskt' concat into 'main | Veg: alt'."""
    parts = _VEG_SPLIT.split(text, maxsplit=1)
    if len(parts) == 2:
        main = parts[0].strip()
        veg = parts[1].strip()
        if main and veg:
            return f"{main} | Veg: {veg}"
        if main:
            return main
        if veg:
            return f"Veg: {veg}"
    return text.strip()


def _extract_meal(cell: Tag) -> str:
    text = cell.get_text(separator="\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return ""
    return format_meal_text(" ".join(lines))


def parse_lunch(html: str, *, school: str, requested_week: int | None = None) -> LunchWeek:
    """Parse a SchoolSoft lunch menu HTML page into a LunchWeek model."""
    soup = BeautifulSoup(html, "lxml")
    now = datetime.now()
    week = requested_week if requested_week is not None else now.isocalendar()[1]
    year = now.year

    meals_by_day: dict[str, str] = {day: "" for day in WEEKDAYS}

    cells = soup.find_all(
        "td", style=lambda s: bool(s) and "word-wrap" in s.lower()
    )
    if cells:
        for i, cell in enumerate(cells[: len(WEEKDAYS)]):
            meals_by_day[WEEKDAYS[i]] = _extract_meal(cell)
    else:
        for table in soup.find_all("table"):
            day_idx = 0
            for row in table.find_all("tr"):
                cells_in_row = row.find_all("td")
                if len(cells_in_row) >= 2:
                    meal_text = _extract_meal(cells_in_row[-1])
                    if meal_text and day_idx < len(WEEKDAYS):
                        meals_by_day[WEEKDAYS[day_idx]] = meal_text
                        day_idx += 1
            if day_idx > 0:
                break

    if not any(meals_by_day.values()):
        logger.warning(
            "Lunch parser found no meals — page layout may have changed. "
            "First 300 chars: %s",
            html[:300],
        )

    return LunchWeek(
        week=week,
        year=year,
        school=school,
        days=[LunchDay(day=d, meal=meals_by_day[d]) for d in WEEKDAYS],
    )
