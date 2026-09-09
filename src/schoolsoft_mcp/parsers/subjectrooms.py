"""Subject rooms (ämnesrum) — the modern planning/assignment surface.

SchoolSoft's React parent UI hangs everything a guardian actually reads off
``/<school>/rest-api/<usertype>/ps/subjectroom/*``. The start-page endpoints
that :mod:`.homework` wraps are only the *teasers* for that surface: they
carry a title and a glued-together subtitle, and nothing else. The body a
teacher writes — "v.37 Orientering (samling vid klubbstugan)" — lives one
call further in, behind ``ps/planning_parts/<id>/view``.

Endpoints used here, all relative to the school root and all prefixed with
the caller's usertype (``parent`` / ``student``):

``ps/subjectroom/all``
    Every subject room the child is in: ``activityId``, subject name,
    groups, colour. ``activityId`` is the join key for everything else.
``ps/subjectroom/<activityId>/teachers``
    Who teaches it.
``ps/subjectroom/plannings/grid/rows``
    Every planning, with **real ISO dates** (``startDate``/``endDate``),
    teacher and subject — unlike the start-page list, which only has a
    Swedish prose date range and only covers one week.
``ps/planning_parts/<partId>/view``
    ``{title, description, publishDate, subtitle}``. ``description`` is
    the teacher's HTML body. This is the payload everything else exists
    to locate.
``ps/subjectroom/assignments/grid/rows`` / ``ps/assignments/<id>/view``
    Same two-step shape for assignments (läxor, prov, inlämningar).
``ps/material/<partId>/file`` and ``.../link``
    Attachments and links hung on a planning or assignment.
``calendar/subject_room/exam-schedule``
    Announced exams, independent of the week you asked about.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from ..models import (
    DAY_KEYS,
    DAY_NAMES_SV,
    DayLesson,
    ExamEntry,
    ExamSchedule,
    Lesson,
    MaterialLink,
    PlanningDetail,
    PlanningPart,
    SubjectRoom,
    SubjectRoomList,
)
from ._fields import int_field, iso_date, str_field
from .homework import split_subtitle

__all__ = ["DAY_KEYS", "DAY_NAMES_SV"]  # re-exported: callers join on these

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths. ``{ut}`` is the usertype segment ("parent" or "student").
# ---------------------------------------------------------------------------
# Only paths this module actually calls live here. Others exist on the REST
# surface (``ps/subjectroom/{id}``, ``.../unread_entities``,
# ``ps/plannings/{id}/view``, ``.../planning_parts/tabs``,
# ``ps/subjectroom/results/grid/rows``, ``.../table/rows``) but have never
# been exercised against a live tenant, and a constant that looks tested
# and is not is worse than no constant. See docs/rest-surface.md.
ROOMS_ALL = "rest-api/{ut}/ps/subjectroom/all"
ROOM_TEACHERS = "rest-api/{ut}/ps/subjectroom/{activity_id}/teachers"

PLANNING_ROWS = "rest-api/{ut}/ps/subjectroom/plannings/grid/rows"
PLANNING_PART_VIEW = "rest-api/{ut}/ps/planning_parts/{part_id}/view"

ASSIGNMENT_ROWS = "rest-api/{ut}/ps/subjectroom/assignments/grid/rows"
ASSIGNMENT_VIEW = "rest-api/{ut}/ps/assignments/{assignment_id}/view"

MATERIAL_FILES = "rest-api/{ut}/ps/material/{section_id}/file"
MATERIAL_LINKS = "rest-api/{ut}/ps/material/{section_id}/link"

EXAM_SCHEDULE = "rest-api/{ut}/calendar/subject_room/exam-schedule"
LESSON_DETAIL = "rest-api/{ut}/calendar/lessons/{lesson_id}"


def path(template: str, usertype: int, **kwargs: Any) -> str:
    """Fill ``{ut}`` from a numeric SchoolSoft usertype plus any other slots."""
    return template.format(ut=usertype_segment(usertype), **kwargs)


def usertype_segment(usertype: int) -> str:
    """``2`` -> ``"parent"``. SchoolSoft namespaces the REST API by role."""
    return {1: "student", 2: "parent", 3: "teacher"}.get(usertype, "parent")


# ---------------------------------------------------------------------------
# HTML bodies
# ---------------------------------------------------------------------------
_WS_RUN = re.compile("[ \t\u00a0]+")  # space, tab, NBSP
_BLANK_RUN = re.compile(r"\n{3,}")


def html_to_text(html: str, *, max_chars: int | None = None) -> str:
    """Flatten a teacher's WYSIWYG body to readable plain text.

    Teachers write these in TinyMCE, so the markup is a grab-bag of
    ``<p>``, ``<ul>``, ``<br>`` and, for term plans, a big
    ``<table>``. Block boundaries become newlines and table cells become
    ``" | "``-separated rows, because a term plan collapsed into one run-on
    line is worse than useless: the per-week rows are exactly the part a
    parent needs to read.
    """
    if not html:
        return ""
    if "<" not in html:
        return _tidy(html, max_chars)

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()

    # Keep link targets — planning bodies link to YouTube clips, Drive docs
    # and Classroom assignments, and a bare anchor text loses all of it.
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        label = a.get_text(" ", strip=True)
        if href and label and href not in label:
            a.replace_with(f"{label} ({href})")

    for br in soup.find_all("br"):
        br.replace_with("\n")

    for table in soup.find_all("table"):
        _render_table(table)
    for row in soup.find_all("tr"):  # stray rows outside any table
        _render_row(row, week_col=None)

    for tag in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "table"]):
        tag.insert_before("\n")
        tag.insert_after("\n")

    return _tidy(soup.get_text(""), max_chars)


# A term plan is often a table whose first column is the week: the header
# says "Vecka" and the rows say a bare "34" or "34-36". Nothing on a row
# then names a week the way prose does, so the week lookup finds nothing at
# all unless the header is carried down onto each row.
_WEEK_HEADER = re.compile(r"^(?:v|ve?cka?|veckor|week)\.?$", re.IGNORECASE)
_BARE_WEEK = re.compile(r"^\d{1,2}(?:\s*[-\u2013\u2014/]\s*\d{1,2})?$")


def _render_table(table: Any) -> None:
    """Flatten one table to ``" | "`` rows, tagging its week column."""
    week_col: int | None = None
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        if week_col is None:
            texts = [c.get_text(" ", strip=True) for c in cells]
            for index, text in enumerate(texts):
                if _WEEK_HEADER.match(text):
                    week_col = index
                    break
            if week_col is not None:
                _render_row(row, week_col=None)  # the header itself
                continue
        _render_row(row, week_col=week_col)


def _render_row(row: Any, *, week_col: int | None) -> None:
    cells = row.find_all(["td", "th"])
    if not cells:
        return
    texts = [c.get_text(" ", strip=True) for c in cells]
    if (
        week_col is not None
        and week_col < len(texts)
        and _BARE_WEEK.match(texts[week_col])
    ):
        texts[week_col] = f"v.{texts[week_col]}"
    row.replace_with("\n" + " | ".join(texts) + "\n")


def _tidy(text: str, max_chars: int | None) -> str:
    text = text.replace(" ", " ")  # noqa: RUF001 - NBSP is the point
    text = "\n".join(_WS_RUN.sub(" ", line).strip() for line in text.splitlines())
    text = _BLANK_RUN.sub("\n\n", text).strip()
    return truncate(text, max_chars)


def truncate(text: str, max_chars: int | None) -> str:
    """Cut ``text`` to ``max_chars``, marking the cut so a reader can see it.

    Anything derived from the text — above all the week lines — must be
    extracted *before* this runs. A term plan's December row sits several
    thousand characters in, and cutting first makes it look as though the
    teacher wrote nothing about December.
    """
    if max_chars is None or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


# ---------------------------------------------------------------------------
# Week references inside a body
# ---------------------------------------------------------------------------
# "v.37", "v 37", "V. 35:", "vecka 34", "Vecka 34-36", "v.34-38", "v34/35".
_WEEK_REF = re.compile(
    r"""(?:^|[\s(\[|])           # start of line or a separator
        (?:v|ve?cka?)\.?\s*      # v / v. / vecka / vcka
        (\d{1,2})                # first week number
        (?:\s*[-\u2013\u2014/]\s*(\d{1,2}))?   # optional range or alternative
    """,
    re.IGNORECASE | re.VERBOSE,
)


def week_references(line: str) -> set[int]:
    """ISO week numbers a line of planning text refers to.

    ``"v.38 Terränglöpning, samling vid spårcentralen 8:20"`` -> ``{38}``;
    ``"Vecka 34-36"`` -> ``{34, 35, 36}``. Empty when the line names no
    week, which is the common case for prose paragraphs.

    Ranges are only expanded forwards and only up to a term's length —
    ``"v.8-2"`` is far more likely to be a date or a score than a
    year-wrapping week range, and guessing wrong here silently attaches a
    planning to the wrong week.
    """
    weeks: set[int] = set()
    for first, second in _WEEK_REF.findall(line):
        start = int(first)
        if not 1 <= start <= 53:
            continue
        if not second:
            weeks.add(start)
            continue
        end = int(second)
        if 1 <= end <= 53 and 0 <= end - start <= 25:
            weeks.update(range(start, end + 1))
        else:
            weeks.add(start)
    return weeks


def lines_for_week(body: str, week: int) -> list[str]:
    """Lines of ``body`` that explicitly name ``week``.

    A term-long planning ("Idrott och hälsa terminen", 19 aug - 31 dec) is
    *in force* every single school day, so date-range filtering alone
    surfaces it every day with 18 weeks of content attached. The teacher
    already solved this by writing one line per week; this pulls out the
    line that is about the week you asked for.
    """
    if not body:
        return []
    out: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or week not in week_references(line):
            continue
        # "Vecka 37-39:" on its own is a heading the teacher put above the
        # real content, not content. Keeping it adds a line that looks like
        # an answer and isn't.
        if len(_WEEK_REF.sub(" ", line).strip(" .:;-\u2013\u2014|")) < 3:
            continue
        if line not in out:
            out.append(line)
    return out


def mentions_any_week(body: str) -> bool:
    """True when the body is organised by week number at all."""
    return any(week_references(line) for line in (body or "").splitlines())


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
parse_iso_date = iso_date
"""``"2026-09-14 15:20"`` / ``"2026-09-14"`` -> ``date``. None if unparsable."""


_SV_MONTHS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
}
_SHORT_DATE = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(_SV_MONTHS) + r")\.?\b", re.IGNORECASE
)


def parse_loose_date(raw: str | None, *, near: dt.date) -> dt.date | None:
    """ISO dates, plus the ``"10 maj"`` form the news list uses.

    The news parser normalises to a bare day and Swedish month with no
    year, so an ISO-only parser reads every news item as undatable — and a
    filter that keeps the undatable keeps the entire feed. The year is the
    one that puts the date nearest ``near``, since a news list spans a term
    and straddles new year.
    """
    iso = parse_iso_date(raw)
    if iso is not None:
        return iso
    if not raw:
        return None
    m = _SHORT_DATE.search(raw)
    if not m:
        return None
    day, month = int(m.group(1)), _SV_MONTHS[m.group(2).lower()]
    best: dt.date | None = None
    for year in (near.year - 1, near.year, near.year + 1):
        try:
            candidate = dt.date(year, month, day)
        except ValueError:  # 29 feb in a non-leap year
            continue
        if best is None or abs((candidate - near).days) < abs((best - near).days):
            best = candidate
    return best


def week_bounds(year: int, week: int) -> tuple[dt.date, dt.date]:
    """Monday and Sunday of an ISO week.

    Raises ``ValueError`` naming the argument. ``fromisocalendar`` alone
    says "Invalid week: 54", which reads like a server fault rather than a
    bad parameter to whoever called the tool.
    """
    try:
        monday = dt.date.fromisocalendar(year, week, 1)
    except ValueError as err:
        raise ValueError(
            f"week must be a valid ISO week for {year} (1-52, or 53 in a long "
            f"year); got {week}"
        ) from err
    return monday, monday + dt.timedelta(days=6)


def overlaps(
    start: dt.date | None, end: dt.date | None, first: dt.date, last: dt.date
) -> bool:
    """True when ``[start, end]`` intersects ``[first, last]``.

    A missing bound is treated as open-ended rather than as a mismatch:
    SchoolSoft leaves ``endDate`` empty on plannings with no stated end,
    and dropping those would hide exactly the long-running ones (term
    plans, "Idrott och hälsa terminen") that carry the week-by-week detail.
    """
    if start is not None and start > last:
        return False
    return not (end is not None and end < first)


# ---------------------------------------------------------------------------
# Payload parsers
# ---------------------------------------------------------------------------
_s = str_field
_i = int_field


def subject_names(entry: dict[str, Any]) -> str:
    """Flatten the ``subjects: [{name, color}]`` shape to ``"Bild, Slöjd"``."""
    subjects = entry.get("subjects")
    if isinstance(subjects, str):
        return subjects.strip()
    if not isinstance(subjects, list):
        return ""
    names = [
        s.get("name", "").strip()
        for s in subjects
        if isinstance(s, dict) and s.get("name")
    ]
    return ", ".join(n for n in names if n)


def parse_rooms(payload: Any, *, school: str) -> SubjectRoomList:
    rooms: list[SubjectRoom] = []
    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            activity_id = _i(entry, "activityId")
            if activity_id is None:
                continue
            groups = entry.get("groupNames")
            rooms.append(
                SubjectRoom(
                    activity_id=activity_id,
                    subject=_s(entry, "subject"),
                    groups=[g for g in groups if isinstance(g, str)]
                    if isinstance(groups, list)
                    else [],
                    color=_s(entry, "color"),
                    has_access=bool(entry.get("access")),
                )
            )
    rooms.sort(key=lambda r: (r.subject.lower(), r.activity_id))
    return SubjectRoomList(
        school=school,
        rooms=rooms,
        note=None
        if rooms
        else (
            "No subject rooms returned. On a parent account this usually "
            "means no child is selected — pass student_id."
        ),
    )


def parse_teachers(payload: Any) -> list[str]:
    """``[{firstName, lastName, id, role}]`` -> ``["Kim Larsson", …]``."""
    if not isinstance(payload, list):
        return []
    names: list[str] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        full = " ".join(
            part
            for part in (_s(entry, "firstName"), _s(entry, "lastName"))
            if part
        )
        if full and full not in names:
            names.append(full)
    return names


def parse_planning_row(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalise one ``plannings/grid/rows`` entry into flat keyword args."""
    return {
        "part_id": _i(entry, "planningPartId"),
        "planning_id": _i(entry, "planningId"),
        "activity_id": _i(entry, "activityId"),
        "title": _s(entry, "planningPartTitle") or _s(entry, "planningTitle"),
        "planning_title": _s(entry, "planningTitle"),
        "subject": subject_names(entry),
        "teacher": _s(entry, "teacher"),
        "start_date": _s(entry, "startDate") or None,
        "end_date": _s(entry, "endDate") or None,
        "publish_date": _s(entry, "publishDate") or None,
        "status": _s(entry, "status"),
        "read": bool(entry.get("read")),
    }


def parse_assignment_row(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalise one ``assignments/grid/rows`` entry into flat keyword args."""
    return {
        "assignment_id": _i(entry, "assignmentId"),
        "activity_id": _i(entry, "activityId"),
        "title": _s(entry, "title"),
        "subject": subject_names(entry),
        "kind": _s(entry, "assignmentType"),
        "teacher": _s(entry, "teacher"),
        "start_date": _s(entry, "startDate") or None,
        "end_date": _s(entry, "endDate") or None,
        "publish_date": _s(entry, "publishDate") or None,
        "status": _s(entry, "status"),
        "submission_status": _s(entry, "submissionStatus"),
        "result_status": _s(entry, "resultReportStatus"),
        "submission_date": _s(entry, "submissionDate") or None,
        "read": bool(entry.get("read")),
    }


def row_fingerprint(row: Any) -> tuple[str, ...]:
    """What about a grid row would move if its planning changed.

    SchoolSoft offers no ETag or modification time on a planning body, so
    the grid row is the only cheap freshness signal: a re-published part
    moves ``publishDate``, an edited one is marked unread again, a re-dated
    one moves its bounds. Any of those should invalidate a cached body.
    None of them is a guarantee, which is why the cache also has a TTL.

    Accepts the dict ``parse_planning_row`` returns or a ``PlanningPart``.
    """
    if isinstance(row, dict):
        def get(key: str) -> Any:
            return row.get(key)
    else:
        def get(key: str) -> Any:
            return getattr(row, key, None)
    return tuple(
        str(get(key) if get(key) is not None else "")
        for key in ("publish_date", "title", "start_date", "end_date", "status", "read")
    )


def parse_detail_view(
    payload: Any, *, week: int | None = None, max_body_chars: int | None = None
) -> dict[str, Any]:
    """Parse a ``.../view`` payload (planning part or assignment).

    Both return ``{title, description, publishDate, subtitle}``; the
    assignment variant adds ``id``, ``type`` and ``subjectNames``.
    """
    if not isinstance(payload, dict):
        return {}
    # Flatten in full, pull the week lines out of the *whole* text, and only
    # then truncate. A term plan runs to several pages and the week you asked
    # about is as likely to be on the last page as the first.
    full_body = html_to_text(payload.get("description") or "")
    out: dict[str, Any] = {
        "title": _s(payload, "title"),
        "subtitle": _s(payload, "subTitle") or _s(payload, "subtitle"),
        "publish_date": _s(payload, "publishDate") or None,
        "body": truncate(full_body, max_body_chars),
        "mentions_weeks": mentions_any_week(full_body),
    }
    out["date_range"], kind, subject = split_subtitle(out["subtitle"])
    if kind:
        out["kind"] = kind
    if subject:
        out["subject"] = subject
    if week is not None:
        out["week_lines"] = lines_for_week(full_body, week)
    # The payload's own fields win over anything peeled off the subtitle.
    if _s(payload, "type"):
        out["kind"] = _s(payload, "type")
    if _s(payload, "subjectNames"):
        out["subject"] = _s(payload, "subjectNames")
    return out


def parse_material(files: Any, links: Any) -> list[MaterialLink]:
    """Merge ``ps/material/<id>/file`` and ``/link`` into one list."""
    out: list[MaterialLink] = []
    if isinstance(files, list):
        for entry in files:
            if not isinstance(entry, dict):
                continue
            out.append(
                MaterialLink(
                    kind="file",
                    name=_s(entry, "displayName")
                    or _s(entry, "fileName")
                    or _s(entry, "name"),
                    url=_s(entry, "url") or None,
                    file_id=_i(entry, "id"),
                )
            )
    if isinstance(links, list):
        for entry in links:
            if not isinstance(entry, dict):
                continue
            out.append(
                MaterialLink(
                    kind="link",
                    name=_s(entry, "displayName") or _s(entry, "name"),
                    url=_s(entry, "url") or None,
                    file_id=_i(entry, "id"),
                )
            )
    return out


def parse_exam_schedule(payload: Any, *, school: str) -> ExamSchedule:
    entries: list[ExamEntry] = []
    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            entries.append(
                ExamEntry(
                    exam_id=_i(entry, "entityId") or _i(entry, "id"),
                    title=_s(entry, "name"),
                    kind=_s(entry, "typeName"),
                    start=_s(entry, "startDate") or None,
                    end=_s(entry, "endDate") or None,
                )
            )
    entries.sort(key=lambda e: e.start or "")
    return ExamSchedule(
        school=school,
        exams=entries,
        note=None if entries else "No announced exams (Prov) found.",
    )


def parse_planning_detail(
    view: dict[str, Any],
    *,
    row: dict[str, Any] | None = None,
    material: list[MaterialLink] | None = None,
    note: str | None = None,
) -> PlanningDetail:
    row = row or {}
    return PlanningDetail(
        part_id=row.get("part_id"),
        planning_id=row.get("planning_id"),
        activity_id=row.get("activity_id"),
        title=view.get("title") or row.get("title", ""),
        subject=view.get("subject") or row.get("subject", ""),
        teacher=row.get("teacher", ""),
        date_range=view.get("date_range", ""),
        start_date=row.get("start_date"),
        end_date=row.get("end_date"),
        publish_date=view.get("publish_date") or row.get("publish_date"),
        status=row.get("status", ""),
        read=bool(row.get("read")),
        body=view.get("body", ""),
        week_lines=view.get("week_lines") or [],
        mentions_weeks=bool(view.get("mentions_weeks")),
        material=material or [],
        note=note,
    )


# ---------------------------------------------------------------------------
# Joining a day's schedule to the plannings that apply to it
# ---------------------------------------------------------------------------
# Subjects where the day itself imposes preparation at home.
#
# Two kinds of needle, because SchoolSoft names the same lesson three ways
# depending on which endpoint you ask: the schedule says "ID", the subject
# room says "Idrott och hälsa", the planning title says "Idrott och hälsa
# HT-26". ``codes`` are matched as whole tokens — "id" as a substring also
# matches "Bild" — while ``names`` are matched as substrings.
PREP_SUBJECTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("idrott", ("id", "idh", "idr"), ("idrott", "gympa", "friidrott")),
    ("simning", ("sim",), ("simning", "badhus", "simhall")),
    ("hemkunskap", ("hkk",), ("hem- och konsument", "hemkunskap")),
    ("utflykt", (), ("utflykt", "friluftsdag", "studiebesök", "exkursion")),
)

# A teacher writing any of these has said something a family must act on
# before the child leaves — a different meeting point, kit to bring, an
# earlier start. Worth surfacing whatever the subject is, which is the
# general case of the Idrott problem rather than a special case of it.
ACTION_HINTS = re.compile(
    r"\b("
    r"samling|samlas|samlingsplats|möt(?:er|s|as|upp)|träffas"
    r"|ta\s+med|ha\s+med|medtag|packa|glöm\s+inte"
    r"|kläder|skor|stövlar|badkläder|handduk|matsäck|matlåda|vattenflaska"
    r"|cykel|cykla|buss|tåg|avfärd|avresa|återsamling"
    r"|utomhus|ute\b|utflykt|friluftsdag|studiebesök"
    r")",
    re.IGNORECASE,
)


def prep_label(subject: str, planning_titles: list[str]) -> str | None:
    """Which preparation category, if any, a lesson falls into.

    Looks at the lesson's own subject *and* at the titles of the plannings
    joined to it, because the schedule's abbreviation is often too short to
    recognise on its own ("ID") while the planning spells it out.
    """
    text = " ".join([subject, *planning_titles]).lower()
    tokens = set(re.findall(r"[a-zåäöéü]+", text))
    for label, codes, names in PREP_SUBJECTS:
        if tokens.intersection(codes) or any(n in text for n in names):
            return label
    return None


def first_lines(body: str, limit: int = 200) -> str:
    """Opening of a planning body, for plannings with no week-numbered lines."""
    return truncate(" ".join(line for line in body.splitlines() if line.strip()), limit)


def activity_for_lesson(lesson: Lesson, plannings: list[PlanningPart]) -> int:
    """Best-effort ``activity_id`` for a schedule lesson.

    The calendar endpoint returns no ``activityId`` on lessons, so the join
    back to a planning goes through the subject name — and SchoolSoft names
    the same subject differently in the two places. The schedule carries a
    short code in ``subject`` ("ID", "BI") and the full name in ``notes``
    ("Idrott", "Biologi"); the planning carries a third form ("Idrott och
    hälsa").

    Matching is deliberately strict:

    - An exact subject match is tried for both forms. Equality cannot
      confuse a code with a longer name, so this is always safe.
    - Prefix matching is where the code is lossy, so only the full name may
      try it, and a miss there is final — no retry with the code.
    - A prefix match must be unambiguous across all the child's plannings.

    Both rules exist because the short code is dangerously lossy: "BI"
    (Biologi) is a prefix of "Bild", so a permissive match hangs the art
    planning off the biology lesson — telling a parent that biology is doing
    a composition exercise. A missing attachment is a gap the reader can see;
    a wrong one is a gap they cannot.

    Returns ``-1`` when nothing matches unambiguously.
    """
    subject = (lesson.subject or "").strip().lower()
    notes = (lesson.notes or "").strip().lower()

    # (activity_id, subject) for the plannings that can be matched at all.
    usable: list[tuple[int, str]] = [
        (p.activity_id, name)
        for p in plannings
        if p.activity_id is not None and (name := (p.subject or "").strip().lower())
    ]

    # An exact hit is safe from either form: "BI" can only equal a planning
    # whose subject *is* "BI", never "Bild". Both are tried because ``notes``
    # is free text and often an annotation ("Ombyte", "Diagnos") rather than
    # the subject's full name, and dropping ``subject`` on that basis loses
    # the match entirely.
    for candidate in dict.fromkeys(c for c in (notes, subject) if c):
        exact = {aid for aid, name in usable if name == candidate}
        if len(exact) == 1:
            return exact.pop()

    # Prefix matching is where the short code is dangerous, so only the full
    # name is allowed to try it, and a miss there is final.
    candidate = notes or subject
    if candidate:
        prefixed = {
            aid
            for aid, name in usable
            if name.startswith(candidate) or candidate.startswith(name)
        }
        if len(prefixed) == 1:
            return prefixed.pop()
    return -1


def day_lessons(
    lessons: list[Lesson], plannings: list[PlanningPart], day_key: str
) -> list[DayLesson]:
    """The day's lessons in time order, each carrying its planning text."""
    by_activity: dict[int, list[PlanningPart]] = {}
    for item in plannings:
        if item.activity_id is not None:
            by_activity.setdefault(item.activity_id, []).append(item)

    out: list[DayLesson] = []
    for lesson in sorted(
        (le for le in lessons if (le.day or "").lower() == day_key),
        key=lambda x: x.start or "",
    ):
        relevant: list[str] = []
        titles: list[str] = []
        activity_id = activity_for_lesson(lesson, plannings)
        for item in by_activity.get(activity_id, []):
            titles.append(item.title)
            if item.week_lines:
                relevant.extend(item.week_lines)
            elif item.body and not item.mentions_weeks:
                # A prose planning with no week numbers anywhere still says
                # what the class is working on; the opening is the useful part.
                #
                # A planning that *is* organised by week and has no line for
                # this one is the opposite: its opening describes some other
                # week. Attaching it here is how "v.34 samling vid
                # klubbstugan" ends up presented as today's meeting point in
                # November. Leave it out and let the caller say that the
                # planning exists but is silent about this week.
                relevant.append(first_lines(item.body))
        out.append(
            DayLesson(
                start=lesson.start,
                end=lesson.end,
                subject=lesson.subject,
                teacher=lesson.teacher,
                room=lesson.room,
                activity_id=activity_id if activity_id >= 0 else None,
                attendance_status=lesson.attendance_status,
                is_break=lesson.is_break,
                planning_titles=titles,
                plannings=relevant,
            )
        )
    return out


def preparation_notes(lessons: list[DayLesson], *, week: int) -> list[str]:
    """The short list a parent acts on: kit, meeting points, missing plannings.

    Every entry is derived from fetched data. A preparation-heavy lesson
    with no published planning says exactly that instead of guessing what
    to bring — a wrong guess here is worse than a gap, because the family
    stops checking the veckobrev.
    """
    notes: list[str] = []
    seen: set[str] = set()
    for lesson in lessons:
        if lesson.is_break:
            continue
        label = prep_label(lesson.subject, lesson.planning_titles)
        actionable = [line for line in lesson.plannings if ACTION_HINTS.search(line)]
        if label is None and not actionable:
            continue
        when = f"{lesson.start}\u2013{lesson.end}".strip("\u2013")
        head = f"{lesson.subject or label} {when}".strip()
        # A line that says where to meet is the point; when there is one, the
        # rest of the planning is noise in a list meant to be read at 07:15.
        lines = actionable or lesson.plannings
        if lines:
            entries = [f"{head}: {line}" for line in lines]
        elif lesson.planning_titles:
            entries = [
                f"{head}: planering finns ({'; '.join(lesson.planning_titles)}) "
                f"men inget står om v.{week}"
            ]
        else:
            entries = [f"{head}: ingen planering publicerad — kolla veckobrevet"]
        for entry in entries:
            if entry not in seen:
                seen.add(entry)
                notes.append(entry)
    return notes
