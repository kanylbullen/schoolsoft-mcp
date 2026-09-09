"""Homework + planning parsers — REST first, JSP fallback.

Modern paths:

- ``rest-api/parent/ps/assignments/start-page?week=N&year=Y`` — homework /
  läxor.
- ``rest-api/parent/ps/planning_parts/start-page?week=N&year=Y`` — lesson
  plans (planeringsdelar).

Both return a JSON list of items with the same flat shape
(``id``/``activityId``/``title``/``subTitle``/``read``). The ``subTitle``
glues the date range, kind ("Diagnos", "Inlämningsuppgift", "Planering")
and subject with commas — we split it out into structured fields.

The legacy JSP parser (``parse_homework`` from the HTML page) is kept as
a fallback for installations that haven't migrated.
"""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup, Tag

from ..models import HomeworkItem, HomeworkList, PlanningList, PlanningPart
from ._fields import ISO_DATE_RE, int_field, iso_date_str, str_field

logger = logging.getLogger(__name__)

HOMEWORK_PATHS = (
    "jsp/student/right_student_homework.jsp",
    "jsp/student/right_student_assignment.jsp",
)
HOMEWORK_REST_PATH = "rest-api/parent/ps/assignments/start-page"
PLANNING_REST_PATH = "rest-api/parent/ps/planning_parts/start-page"

_DATE_RE = ISO_DATE_RE


def parse_homework_json(
    payload: Any,
    *,
    school: str,
    week: int | None = None,
    year: int | None = None,
) -> HomeworkList:
    """Parse the JSON returned by the assignments REST endpoint."""
    items: list[HomeworkItem] = []
    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            subtitle = _str_field(entry, "subTitle")
            date_range, kind, subject = split_subtitle(subtitle)
            items.append(
                HomeworkItem(
                    title=_str_field(entry, "title"),
                    subject=subject,
                    kind=kind,
                    date_range=date_range,
                    subtitle=subtitle,
                    due=_iso_date(_str_field(entry, "sortDate")),
                    read=bool(entry.get("read", False)),
                    submission_status=_str_field(entry, "submissionStatus"),
                    result_status=_str_field(entry, "resultReportStatus"),
                    assignment_id=_int_field(entry, "id"),
                    activity_id=_int_field(entry, "activityId"),
                    # Keep legacy mirror so callers reading the old shape
                    # still get the same text in `description`.
                    description=subtitle,
                )
            )

    return HomeworkList(
        school=school,
        items=items,
        week=week,
        year=year,
        note=None if items else _empty_note("homework"),
    )


def parse_planning_json(
    payload: Any,
    *,
    school: str,
    week: int | None = None,
    year: int | None = None,
) -> PlanningList:
    """Parse the JSON returned by the planning_parts REST endpoint."""
    items: list[PlanningPart] = []
    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            subtitle = _str_field(entry, "subTitle")
            date_range, kind, subject = split_subtitle(subtitle)
            items.append(
                PlanningPart(
                    title=_str_field(entry, "title"),
                    subject=subject,
                    kind=kind,
                    date_range=date_range,
                    subtitle=subtitle,
                    read=bool(entry.get("read", False)),
                    part_id=_int_field(entry, "id"),
                    planning_id=_int_field(entry, "planningId"),
                    activity_id=_int_field(entry, "activityId"),
                )
            )

    return PlanningList(
        school=school,
        items=items,
        week=week,
        year=year,
        note=None if items else _empty_note("planning"),
    )


# SchoolSoft "type" labels observed in subtitles. Used when the kind is
# glued to the date range with whitespace rather than separated by a comma
# (the planning_parts endpoint does this).
_KNOWN_KINDS = (
    "Planering",
    "Diagnos",
    "Inlämningsuppgift",
    "Test",
    "Prov",
    "Läxa",
    "Annat",
)


def split_subtitle(subtitle: str) -> tuple[str, str, str]:
    """Split a subtitle into (date_range, kind, subject).

    Two observed shapes:
    - assignments: ``"<date_range>, <kind>, <subject>"`` (comma-separated).
    - planning_parts: ``"<date_range> <kind>, <subject>"`` (kind glued to
      the end of the date region with whitespace).

    SchoolSoft may also include commas inside the date range (rare), so
    when comma-splitting yields 3+ parts we take the last two as
    kind + subject and join everything else back as the date range.
    """
    if not subtitle:
        return "", "", ""
    parts = [p.strip() for p in subtitle.split(",")]
    if len(parts) >= 3:
        date_range = ", ".join(parts[:-2])
        kind = parts[-2]
        subject = parts[-1]
        return date_range, kind, subject
    if len(parts) == 2:
        # Try to peel a known kind off the end of the first part
        # (planning_parts case: "tors 08 jan. - tors 18 juni Planering").
        rest = parts[0]
        for kind in _KNOWN_KINDS:
            suffix = " " + kind
            if rest.endswith(suffix):
                return rest[: -len(suffix)].rstrip(), kind, parts[1]
        return rest, "", parts[1]
    return parts[0], "", ""


_str_field = str_field
_int_field = int_field


def _iso_date(raw: str) -> str | None:
    """``"2026-05-20 00:00"`` → ``"2026-05-20"``. None on parse failure."""
    return iso_date_str(raw) or None


def _empty_note(kind: str) -> str:
    return (
        f"No {kind} items found for the requested week. "
        "Either there really aren't any, or the page layout changed — "
        "call dump_json on the REST path and share a sanitised excerpt."
    )


# ----------------------------------------------------------------------------
# Legacy JSP fallback (kept for installations without the REST surface).
# ----------------------------------------------------------------------------


def parse_homework(html: str, *, school: str) -> HomeworkList:
    """Parse the legacy ``right_student_homework.jsp`` page."""
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
