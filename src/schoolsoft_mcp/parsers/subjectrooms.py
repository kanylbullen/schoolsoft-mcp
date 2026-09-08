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
``ps/plannings/<planningId>/planning_parts/tabs``
    The parts a multi-part planning is split into.
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
    ExamEntry,
    ExamSchedule,
    MaterialLink,
    PlanningDetail,
    SubjectRoom,
    SubjectRoomList,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths. ``{ut}`` is the usertype segment ("parent" or "student").
# ---------------------------------------------------------------------------
ROOMS_ALL = "rest-api/{ut}/ps/subjectroom/all"
ROOM_ONE = "rest-api/{ut}/ps/subjectroom/{activity_id}"
ROOM_TEACHERS = "rest-api/{ut}/ps/subjectroom/{activity_id}/teachers"
ROOM_UNREAD = "rest-api/{ut}/ps/subjectroom/unread_entities"

PLANNING_ROWS = "rest-api/{ut}/ps/subjectroom/plannings/grid/rows"
PLANNING_ROWS_FOR_ROOM = (
    "rest-api/{ut}/ps/subjectroom/{activity_id}/plannings/grid/rows"
)
PLANNING_PART_VIEW = "rest-api/{ut}/ps/planning_parts/{part_id}/view"
PLANNING_PART_SECTIONS = "rest-api/{ut}/ps/planning_parts/{part_id}/sections"
PLANNING_VIEW = "rest-api/{ut}/ps/plannings/{planning_id}/view"
PLANNING_PART_TABS = "rest-api/{ut}/ps/plannings/{planning_id}/planning_parts/tabs"

ASSIGNMENT_ROWS = "rest-api/{ut}/ps/subjectroom/assignments/grid/rows"
ASSIGNMENT_VIEW = "rest-api/{ut}/ps/assignments/{assignment_id}/view"
RESULT_ROWS = "rest-api/{ut}/ps/subjectroom/results/grid/rows"
TABLE_ROWS = "rest-api/{ut}/ps/subjectroom/table/rows"

MATERIAL_FILES = "rest-api/{ut}/ps/material/{section_id}/file"
MATERIAL_LINKS = "rest-api/{ut}/ps/material/{section_id}/link"
MATERIAL_FILE_DOWNLOAD = "rest-api/{ut}/ps/material/{section_id}/file/{file_id}"

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

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        text = " | ".join(c.get_text(" ", strip=True) for c in cells)
        row.replace_with(f"\n{text}\n")

    for tag in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "table"]):
        tag.insert_before("\n")
        tag.insert_after("\n")

    return _tidy(soup.get_text(""), max_chars)


def _tidy(text: str, max_chars: int | None) -> str:
    text = text.replace(" ", " ")  # noqa: RUF001 - NBSP is the point
    text = "\n".join(_WS_RUN.sub(" ", line).strip() for line in text.splitlines())
    text = _BLANK_RUN.sub("\n\n", text).strip()
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


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

    A term-long planning ("Idrott och hälsa HT", 19 aug - 31 dec) is
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
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def parse_iso_date(raw: str | None) -> dt.date | None:
    """``"2026-09-14 15:20"`` / ``"2026-09-14"`` -> ``date``. None if unparsable."""
    if not raw:
        return None
    m = _ISO_DATE.search(raw)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def week_bounds(year: int, week: int) -> tuple[dt.date, dt.date]:
    """Monday and Sunday of an ISO week."""
    monday = dt.date.fromisocalendar(year, week, 1)
    return monday, monday + dt.timedelta(days=6)


def overlaps(
    start: dt.date | None, end: dt.date | None, first: dt.date, last: dt.date
) -> bool:
    """True when ``[start, end]`` intersects ``[first, last]``.

    A missing bound is treated as open-ended rather than as a mismatch:
    SchoolSoft leaves ``endDate`` empty on plannings with no stated end,
    and dropping those would hide exactly the long-running ones (term
    plans, "Idrott och hälsa HT") that carry the week-by-week detail.
    """
    if start is not None and start > last:
        return False
    return not (end is not None and end < first)


# ---------------------------------------------------------------------------
# Payload parsers
# ---------------------------------------------------------------------------
def _s(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _i(entry: dict[str, Any], key: str) -> int | None:
    value = entry.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


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


def parse_detail_view(
    payload: Any, *, week: int | None = None, max_body_chars: int | None = None
) -> dict[str, Any]:
    """Parse a ``.../view`` payload (planning part or assignment).

    Both return ``{title, description, publishDate, subtitle}``; the
    assignment variant adds ``id``, ``type`` and ``subjectNames``.
    """
    if not isinstance(payload, dict):
        return {}
    body = html_to_text(payload.get("description") or "", max_chars=max_body_chars)
    out: dict[str, Any] = {
        "title": _s(payload, "title"),
        "subtitle": _s(payload, "subTitle") or _s(payload, "subtitle"),
        "publish_date": _s(payload, "publishDate") or None,
        "body": body,
        "body_html": payload.get("description") or "",
    }
    if week is not None:
        out["week_lines"] = lines_for_week(body, week)
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
) -> PlanningDetail:
    row = row or {}
    return PlanningDetail(
        part_id=row.get("part_id"),
        planning_id=row.get("planning_id"),
        activity_id=row.get("activity_id"),
        title=view.get("title") or row.get("title", ""),
        subject=view.get("subject") or row.get("subject", ""),
        teacher=row.get("teacher", ""),
        date_range=view.get("subtitle", ""),
        start_date=row.get("start_date"),
        end_date=row.get("end_date"),
        publish_date=view.get("publish_date") or row.get("publish_date"),
        status=row.get("status", ""),
        read=bool(row.get("read")),
        body=view.get("body", ""),
        week_lines=view.get("week_lines") or [],
        material=material or [],
    )


# ---------------------------------------------------------------------------
# Joining a day's schedule to the plannings that apply to it
# ---------------------------------------------------------------------------
DAY_KEYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
DAY_NAMES_SV: dict[str, str] = {
    "monday": "måndag",
    "tuesday": "tisdag",
    "wednesday": "onsdag",
    "thursday": "torsdag",
    "friday": "fredag",
    "saturday": "lördag",
    "sunday": "söndag",
}

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
    text = " ".join(line for line in body.splitlines() if line.strip())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def activity_for_lesson(lesson: Any, plannings: list[Any]) -> int:
    """Best-effort ``activity_id`` for a schedule lesson.

    The calendar endpoint returns no ``activityId`` on lessons, so the join
    back to a planning goes through the subject name — and SchoolSoft names
    the same subject differently in the two places. The schedule carries a
    short code in ``subject`` ("ID", "BI") and the full name in ``notes``
    ("Idrott", "Biologi"); the planning carries a third form ("Idrott och
    hälsa").

    Matching is deliberately strict, and prefers the full name:

    - The full name wins outright. If it matches nothing, we stop — we do
      **not** retry with the short code.
    - A prefix match must be unambiguous across all the child's plannings.

    Both rules exist because the short code is dangerously lossy: "BI"
    (Biologi) is a prefix of "Bild", so a permissive match hangs the art
    planning off the biology lesson — telling a parent that biology is doing
    a composition exercise. A missing attachment is a gap the reader can see;
    a wrong one is a gap they cannot.

    Returns ``-1`` when nothing matches unambiguously.
    """
    subject = (getattr(lesson, "subject", "") or "").strip().lower()
    notes = (getattr(lesson, "notes", "") or "").strip().lower()
    candidates = [c for c in (notes, subject) if c]
    # The full name, when SchoolSoft gave us one, is the only candidate.
    if notes and notes != subject:
        candidates = [notes]

    usable = [
        p for p in plannings
        if p.activity_id is not None and (p.subject or "").strip()
    ]
    for candidate in candidates:
        exact = {
            int(p.activity_id)
            for p in usable
            if (p.subject or "").strip().lower() == candidate
        }
        if len(exact) == 1:
            return exact.pop()
        prefixed = {
            int(p.activity_id)
            for p in usable
            if (target := (p.subject or "").strip().lower())
            and (target.startswith(candidate) or candidate.startswith(target))
        }
        if len(prefixed) == 1:
            return prefixed.pop()
    return -1


def day_lessons(lessons: list[Any], plannings: list[Any], day_key: str) -> list[Any]:
    """The day's lessons in time order, each carrying its planning text."""
    from ..models import DayLesson

    by_activity: dict[int, list[Any]] = {}
    for item in plannings:
        if item.activity_id is not None:
            by_activity.setdefault(item.activity_id, []).append(item)

    out: list[Any] = []
    for lesson in sorted(
        (le for le in lessons if (le.day or "").lower() == day_key),
        key=lambda x: x.start or "",
    ):
        relevant: list[str] = []
        titles: list[str] = []
        for item in by_activity.get(activity_for_lesson(lesson, plannings), []):
            titles.append(item.title)
            if item.week_lines:
                relevant.extend(item.week_lines)
            elif item.body:
                # A planning with no week-numbered lines still says what the
                # class is working on; the opening lines are the useful part.
                relevant.append(first_lines(item.body))
        out.append(
            DayLesson(
                start=lesson.start,
                end=lesson.end,
                subject=lesson.subject,
                teacher=lesson.teacher,
                room=lesson.room,
                attendance_status=lesson.attendance_status,
                is_break=lesson.is_break,
                planning_titles=titles,
                plannings=relevant,
            )
        )
    return out


def preparation_notes(lessons: list[Any], *, week: int) -> list[str]:
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
