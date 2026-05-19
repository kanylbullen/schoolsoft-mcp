"""Lunch menu parser — REST first, JSP fallback.

The modern path is ``/rest-api/lunchmenu/week/<N>``, which returns a
JSON array (one entry per weekday). The older JSP page at
``jsp/student/right_student_lunchmenu.jsp`` is kept as a fallback for
installations that haven't migrated.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup, Tag

from ..models import WEEKDAYS, LunchDay, LunchWeek

logger = logging.getLogger(__name__)

LUNCH_PATH = "jsp/student/right_student_lunchmenu.jsp"
LUNCH_REST_PATH_TEMPLATE = "rest-api/lunchmenu/week/{week}"

# REST: one entry per weekday with main + vegetarian separated by a
# "\r\n\r\nVegetarisk[t]\r\n" marker. We accept both spellings.
_REST_VEG_SPLIT = re.compile(r"\r?\n\s*\r?\n\s*Vegetarisk[t]?\s*\r?\n", re.IGNORECASE)

# JSP: same idea but with the marker collapsed onto one line of plain text.
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


def parse_lunch_json(
    payload: Any,
    *,
    school: str,
    requested_week: int | None = None,
) -> LunchWeek:
    """Parse the JSON returned by ``/rest-api/lunchmenu/week/<N>``.

    Shape::

        [
          {"dayId": 1, "dishes": [{"dishType": "Dagens lunch",
                                    "dish": "<main>\\r\\n\\r\\nVegetariskt\\r\\n<veg>\\r\\n"}]},
          ...
        ]

    ``dayId`` is 1-based Monday..Friday. Missing days come back with
    empty ``meal``. The ``dish`` text glues main and vegetarian — we
    split on the ``Vegetarisk[t]`` marker and reformat as
    ``"<main> | Veg: <alt>"`` to match the JSP-derived format that
    existing callers may rely on.
    """
    now = datetime.now()
    iso_year, iso_week, _ = now.isocalendar()
    week = requested_week if requested_week is not None else iso_week
    year = iso_year

    meals_by_day: dict[str, str] = {day: "" for day in WEEKDAYS}
    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            day_id = entry.get("dayId")
            if not isinstance(day_id, int) or not 1 <= day_id <= len(WEEKDAYS):
                continue
            dish_texts: list[str] = []
            for dish in entry.get("dishes", []) or []:
                if isinstance(dish, dict):
                    text = dish.get("dish", "")
                    if isinstance(text, str) and text.strip():
                        dish_texts.append(_format_rest_dish(text))
            if dish_texts:
                meals_by_day[WEEKDAYS[day_id - 1]] = " | ".join(dish_texts)

    return LunchWeek(
        week=week,
        year=year,
        school=school,
        days=[LunchDay(day=d, meal=meals_by_day[d]) for d in WEEKDAYS],
    )


def _format_rest_dish(text: str) -> str:
    """Split the dish text on the Vegetarisk[t] marker and reformat."""
    parts = _REST_VEG_SPLIT.split(text, maxsplit=1)
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


def parse_lunch(html: str, *, school: str, requested_week: int | None = None) -> LunchWeek:
    """Parse a SchoolSoft lunch menu HTML page into a LunchWeek model.

    Uses ISO week + ISO year so the pair (week, year) is internally
    consistent at year boundaries — matches ``parse_lunch_json``.
    """
    soup = BeautifulSoup(html, "lxml")
    iso_year, iso_week, _ = datetime.now().isocalendar()
    week = requested_week if requested_week is not None else iso_week
    year = iso_year

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
