"""FastMCP server exposing SchoolSoft data as tools."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import logging
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

import httpx
from mcp.server.fastmcp import Context, FastMCP

from .client import (
    SchoolSoftAccessError,
    SchoolSoftAuthError,
    SchoolSoftClient,
    SchoolSoftConnectionError,
)
from .config import ConfigError, Settings
from .models import (
    AsOf,
    AssessedWork,
    AssessmentDetail,
    AssessmentList,
    AssessmentTerm,
    AttachmentBytes,
    AttachmentText,
    AttendanceReport,
    ChildList,
    ContactList,
    DayBriefing,
    DayLesson,
    ExamSchedule,
    GradeList,
    HomeworkItem,
    HomeworkList,
    LessonDetail,
    LibraryFileList,
    LunchWeek,
    MessageList,
    NewsFeed,
    NewsItem,
    OpenWorkItem,
    OpenWorkList,
    PlanningDetail,
    PlanningList,
    PlanningPart,
    ResultEntry,
    ResultList,
    ScheduleWeek,
    SchoolInformation,
    SubjectAssessment,
    SubjectRoomList,
    UnreportedAbsenceList,
)
from .parsers import assessment as asm
from .parsers import subjectrooms as sr
from .parsers.attachments import (
    build_download_path,
    extract_text,
    filename_from_headers,
    guess_content_type,
)
from .parsers.attendance import (
    ATTENDANCE_PATHS,
    UNREPORTED_ABSENCE_PATHS,
    parse_attendance,
    parse_unreported_absence,
)
from .parsers.children import parse_parent_header
from .parsers.grades import GRADES_PATHS, parse_grades
from .parsers.homework import (
    HOMEWORK_PATHS,
    HOMEWORK_REST_PATH,
    PLANNING_REST_PATH,
    parse_homework,
    parse_homework_json,
    parse_planning_json,
)
from .parsers.lunch import (
    LUNCH_PATH,
    LUNCH_REST_PATH_TEMPLATE,
    parse_lunch,
    parse_lunch_json,
)
from .parsers.misc_jsp import (
    CONTACTS_PATHS,
    LIBRARY_PATHS,
    SCHOOL_INFO_PATHS,
    parse_contacts,
    parse_library_files,
    parse_school_info,
)
from .parsers.news import MESSAGES_PATHS, NEWS_PATHS, parse_messages, parse_news
from .parsers.schedule import (
    SCHEDULE_PATHS,
    SCHEDULE_REST_EVENTS_PATH_TEMPLATE,
    SCHEDULE_REST_LESSONS_PATH_TEMPLATE,
    parse_schedule,
    parse_schedule_json,
)

logger = logging.getLogger(__name__)

# Fetching an *optional* extra — a body, a material list, a teacher list —
# must never take down the listing it decorates. One tuple so the policy is
# changed in one place rather than in the ten spots that used to spell it out.
_REST_ERRORS = (
    SchoolSoftConnectionError,
    SchoolSoftAuthError,
    httpx.HTTPStatusError,
)


async def _fetch_json_or_none(
    app: AppContext, path: str, *, label: str
) -> Any | None:
    """Fetch JSON, or None when the optional extra is unavailable."""
    try:
        return await app.client.fetch_json(path)
    except _REST_ERRORS as err:
        logger.debug("%s unavailable (%s): %s", label, path, err)
        return None


@dataclass(slots=True)
class AppContext:
    settings: Settings
    client: SchoolSoftClient
    lock: asyncio.Lock


def _build_client(settings: Settings) -> SchoolSoftClient:
    return SchoolSoftClient(
        school=settings.school,
        username=settings.username,
        password=settings.password,
        usertype=settings.usertype,
        base_url=settings.base_url,
        timeout=settings.request_timeout,
    )


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    settings = Settings.from_env()
    client = _build_client(settings)
    ctx = AppContext(settings=settings, client=client, lock=asyncio.Lock())
    try:
        yield ctx
    finally:
        await client.close()


mcp: FastMCP = FastMCP("schoolsoft", lifespan=_lifespan)


def _app(ctx: Context[Any, AppContext, Any]) -> AppContext:
    return ctx.request_context.lifespan_context


class _HasAsOf(Protocol):
    """Structural protocol for response models with an ``as_of`` field."""

    as_of: AsOf | None


_AsOfT = TypeVar("_AsOfT", bound=_HasAsOf)


def _now_as_of() -> AsOf:
    """Return the current local date packaged as :class:`AsOf`."""
    today = dt.date.today()
    iso_year, iso_week, _ = today.isocalendar()
    return AsOf(date=today.isoformat(), iso_year=iso_year, iso_week=iso_week)


def _stamp(response: _AsOfT) -> _AsOfT:
    """Populate ``response.as_of`` with the current date.

    Lets the model anchor temporal reasoning — e.g. "this veckobrev is
    dated week 20 but mentions 'kommande vecka', and as_of says week 21,
    so 'kommande vecka' = now".
    """
    response.as_of = _now_as_of()
    return response


NEWS_ITEM_PATH = "jsp/student/right_student_news.jsp"

# SchoolSoft answers a download the session isn't entitled to with a 404 on
# the signed /files/ URL rather than an error page. A 403 never surfaces as
# an HTTPStatusError — fetch_bytes reads it as session expiry and re-tries
# the login first, raising SchoolSoftAccessError if it stays refused.
_ATTACHMENT_RETRY_STATUSES = frozenset({404})

# The JSP copies the attachment to /files/<school>/tmp_file_<id>.tmp and
# redirects to it immediately, so a large file can 404 for a second or two
# while the copy is still landing — observed on a 3.9 MB PDF that the very
# next call fetched fine. Every attempt re-requests the JSP, minting a new
# signed URL rather than replaying the stale one.
_ATTACHMENT_RETRY_DELAYS = (0.0, 1.5, 4.0)


async def _select_child(
    app: AppContext, student_id: int | None, *, org_id: int | None = None
) -> None:
    """Point the session at ``student_id``. Caller must hold ``app.lock``.

    No-op when ``student_id`` is ``None`` or already selected. Looks the
    child's ``org_id`` up from the parent header when the caller didn't
    supply one: it is school-specific, and sending the wrong one leaves the
    session on the previous child while still answering 200.
    """
    if student_id is None:
        return
    current = app.client.active_child
    already_selected = (
        current is not None
        and current[0] == student_id
        and (org_id is None or current[1] == org_id)
    )
    if already_selected:
        return
    if org_id is None:
        org_id = await _resolve_org_id(app, student_id)
    await app.client.select_child(student_id, org_id)


async def _resolve_org_id(app: AppContext, student_id: int) -> int:
    """Find ``student_id``'s org ID on the parent header. Lock must be held."""
    payload = await app.client.fetch_json(SchoolSoftClient.PARENT_HEADER_PATH)
    children = parse_parent_header(payload, school=app.settings.school)
    match = next((c for c in children.children if c.student_id == student_id), None)
    if match is None:
        known = ", ".join(str(c.student_id) for c in children.children) or "none"
        raise ValueError(
            f"student_id {student_id} is not on this parent account "
            f"(known ids: {known}). Call list_children() first."
        )
    if match.org_id is not None:
        return match.org_id
    if match.active and children.active_org_id is not None:
        return children.active_org_id
    # Guessing here is worse than failing: SchoolSoft accepts a wrong orgId
    # with a 200 and leaves the previous child selected, so every following
    # call would quietly answer for the wrong kid.
    raise ValueError(
        f"SchoolSoft's parent header carries no orgId for student_id "
        f"{student_id}, so the school it belongs to can't be determined. "
        "Pass org_id= explicitly (it is the orgId in the school's URL / the "
        "parent header payload)."
    )


async def _fetch_attachment(
    app: AppContext,
    *,
    news_id: int,
    fileid: int,
    type_id: int,
    object_kind: str,
) -> tuple[bytes, dict[str, str], str | None]:
    """Download an attachment. Returns ``(content, headers, note)``.

    A non-empty ``note`` means the download failed in a way the caller can
    act on — content and headers are then empty. Lock must be held.
    """
    path, params = build_download_path(
        parent_id=news_id, type_id=type_id, fileid=fileid, object_kind=object_kind
    )
    try:
        content, headers = await app.client.fetch_bytes(path, params=params)
        return content, headers, None
    except httpx.HTTPStatusError as err:
        if err.response.status_code not in _ATTACHMENT_RETRY_STATUSES:
            raise
        last_failure: Exception = err
    except SchoolSoftAccessError as err:
        # Valid session, resource still refused — the wrong-child signature.
        last_failure = err

    if object_kind != "news":
        return b"", {}, _attachment_failure_note(
            last_failure, news_id=news_id, fileid=fileid
        )

    # The JSP mints a signed /files/ URL only for a file the session is
    # entitled to right now, and a browser always has the news item open
    # when its download link is clicked. Reproduce that state before
    # retrying — it costs one GET, and only on the failing path.
    try:
        await app.client.fetch_html(
            NEWS_ITEM_PATH,
            params={"requestid": str(news_id), "type": str(type_id), "action": "view"},
        )
    except (
        httpx.HTTPStatusError,
        SchoolSoftConnectionError,
        SchoolSoftAccessError,
    ) as err:
        logger.debug("Could not open news item %s before retrying: %s", news_id, err)

    for delay in _ATTACHMENT_RETRY_DELAYS:
        if delay:
            await asyncio.sleep(delay)
        try:
            content, headers = await app.client.fetch_bytes(path, params=params)
            return content, headers, None
        except httpx.HTTPStatusError as err:
            if err.response.status_code not in _ATTACHMENT_RETRY_STATUSES:
                raise
            last_failure = err
        except (SchoolSoftConnectionError, SchoolSoftAccessError) as err:
            # A plain SchoolSoftAuthError (bad credentials) is deliberately
            # not caught — no student_id or delay fixes that.
            last_failure = err

    return b"", {}, _attachment_failure_note(last_failure, news_id=news_id, fileid=fileid)


def _attachment_failure_note(err: Exception, *, news_id: int, fileid: int) -> str:
    detail = (
        f"HTTP {err.response.status_code}"
        if isinstance(err, httpx.HTTPStatusError)
        else str(err)
    )
    attempts = 1 + len(_ATTACHMENT_RETRY_DELAYS)
    window = sum(_ATTACHMENT_RETRY_DELAYS)
    return (
        f"Could not download fileid {fileid} of news item {news_id} ({detail}) "
        f"after {attempts} attempts over {window:.0f}s. SchoolSoft only serves "
        "attachments belonging to the child currently selected in the session: "
        "if this item is another child's, call list_children() and pass that "
        "child's student_id to this tool. If the right child is already "
        "selected, the file is missing on SchoolSoft's file server — open the "
        "item in the web UI to confirm."
    )


async def _fetch_first(
    client: SchoolSoftClient,
    paths: tuple[str, ...],
    params: dict[str, str] | None = None,
) -> str:
    """Try each path until one returns HTML; raise the last error if all fail."""
    last_error: Exception | None = None
    for path in paths:
        try:
            return await client.fetch_html(path, params=params)
        except (SchoolSoftConnectionError, SchoolSoftAuthError) as err:
            last_error = err
            logger.debug("Path %s failed: %s", path, err)
    assert last_error is not None
    raise last_error


@mcp.tool()
async def list_children(ctx: Context[Any, AppContext, Any]) -> ChildList:
    """List the children attached to this parent account.

    Returns one ``Child`` per kid with their ``student_id``, name, school/grade
    label, and an ``active`` flag for the one currently selected in
    SchoolSoft's session. Call this first if you have multiple children — the
    SchoolSoft session has exactly one "active" child at a time, and tools
    like ``get_schedule`` / ``get_lunch_menu`` return data for whichever
    child is currently active. Switch with ``set_active_child``.

    Each child also carries the ``org_id`` its school uses — pass it along
    with ``student_id`` when switching if you want to skip the extra lookup.
    """
    app = _app(ctx)
    async with app.lock:
        payload = await app.client.fetch_json(SchoolSoftClient.PARENT_HEADER_PATH)
    return _stamp(parse_parent_header(payload, school=app.settings.school))


@mcp.tool()
async def set_active_child(
    ctx: Context[Any, AppContext, Any],
    student_id: int,
    org_id: int | None = None,
) -> ChildList:
    """Make ``student_id`` the active child in SchoolSoft's session.

    Subsequent calls to schedule / lunch / homework / planning / news return
    data for this child, and it stays selected across session expiry. Leave
    ``org_id`` unset to have it looked up from ``list_children`` — it is
    school-specific, and passing a wrong one is accepted silently while the
    session stays on the previous child. Returns the refreshed child list
    (with the new active flag) so the caller can confirm the switch.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id, org_id=org_id)
        payload = await app.client.fetch_json(SchoolSoftClient.PARENT_HEADER_PATH)
    return _stamp(parse_parent_header(payload, school=app.settings.school))


@mcp.tool()
async def dump_json(
    ctx: Context[Any, AppContext, Any],
    path: str,
    method: str = "GET",
) -> Any:
    """Fetch a ``rest-api/*`` endpoint and return the raw parsed JSON.

    Companion to ``dump_page`` for debugging the REST surface. Use sparingly
    — responses include personal data (names, grades, message content) and
    will be sent to whatever LLM is reading this tool's output. Sanitise
    before sharing publicly.
    """
    app = _app(ctx)
    async with app.lock:
        return await app.client.fetch_json(path, method=method.upper())


@mcp.tool()
async def get_lunch_menu(
    ctx: Context[Any, AppContext, Any],
    week: int | None = None,
    student_id: int | None = None,
) -> LunchWeek:
    """Return the lunch menu for the given ISO week (defaults to current week).

    Uses SchoolSoft's modern JSON REST endpoint when available and falls
    back to the legacy JSP page if the REST call fails (older installs).
    """
    app = _app(ctx)
    actual_week = week if week is not None else _now_as_of().iso_week
    rest_path = LUNCH_REST_PATH_TEMPLATE.format(week=actual_week)

    async with app.lock:
        await _select_child(app, student_id)
        try:
            payload = await app.client.fetch_json(rest_path)
        except _REST_ERRORS as err:
            logger.debug("Lunch REST failed (%s), falling back to JSP", err)
            params = {"requestid": str(actual_week)}
            html = await app.client.fetch_html(LUNCH_PATH, params=params)
            return _stamp(
                parse_lunch(
                    html, school=app.settings.school, requested_week=actual_week
                )
            )

    return _stamp(
        parse_lunch_json(
            payload, school=app.settings.school, requested_week=actual_week
        )
    )


@mcp.tool()
async def get_schedule(
    ctx: Context[Any, AppContext, Any],
    week: int | None = None,
    year: int | None = None,
    student_id: int | None = None,
) -> ScheduleWeek:
    """Return the schedule for the given ISO week (defaults to current week).

    Uses the REST endpoint when available and falls back to the legacy
    JSP scraper on non-2xx. Lessons come with start/end times, subject,
    teacher, room, teaching group, lesson_id (useful for cross-
    referencing absence), and any reported per-student attendance status.
    All-day events (sport days etc.) come from a sibling endpoint and
    are merged into ``all_day_events``.
    """
    app = _app(ctx)
    now = _now_as_of()
    actual_week = week if week is not None else now.iso_week
    actual_year = year if year is not None else now.iso_year
    lessons_path = SCHEDULE_REST_LESSONS_PATH_TEMPLATE.format(week=actual_week)
    events_path = SCHEDULE_REST_EVENTS_PATH_TEMPLATE.format(
        week=actual_week, year=actual_year
    )

    async with app.lock:
        await _select_child(app, student_id)
        try:
            lessons_payload = await app.client.fetch_json(lessons_path)
        except _REST_ERRORS as err:
            logger.debug("Schedule REST failed (%s), falling back to JSP", err)
            params = {"requestid": str(actual_week)}
            html = await _fetch_first(app.client, SCHEDULE_PATHS, params=params)
            return _stamp(
                parse_schedule(
                    html,
                    school=app.settings.school,
                    requested_week=actual_week,
                    requested_year=actual_year,
                )
            )

        # Events endpoint may legitimately return [] or 404 for weeks
        # without any all-day items — swallow only 404 (and explicit
        # auth/connection errors) so real 5xx failures still surface.
        try:
            events_payload = await app.client.fetch_json(events_path)
        except httpx.HTTPStatusError as err:
            if err.response.status_code == 404:
                logger.debug("No all-day events for w%d/%d (404)", actual_week, actual_year)
                events_payload = None
            else:
                logger.warning(
                    "Schedule events fetch returned %d; continuing without",
                    err.response.status_code,
                )
                events_payload = None
        except (SchoolSoftConnectionError, SchoolSoftAuthError) as err:
            logger.debug("Schedule events fetch failed (%s); continuing without", err)
            events_payload = None

    return _stamp(
        parse_schedule_json(
            lessons_payload,
            events_payload,
            school=app.settings.school,
            week=actual_week,
            year=actual_year,
        )
    )


@mcp.tool()
async def get_school_info(
    ctx: Context[Any, AppContext, Any],
    student_id: int | None = None,
) -> SchoolInformation:
    """Return the Skolinformation page as plain text.

    The page is free-form CMS-edited HTML (school hours, phone numbers,
    term dates, addresses, …), so we don't try to impose structure —
    just extract the visible text and let the caller interpret it.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        html = await _fetch_first(app.client, SCHOOL_INFO_PATHS)
    return _stamp(parse_school_info(html, school=app.settings.school))


@mcp.tool()
async def get_contacts(
    ctx: Context[Any, AppContext, Any],
    student_id: int | None = None,
) -> ContactList:
    """Return the class contact list for the active child.

    Maps to Skolinfo → Kontaktlistor. Each :class:`Contact` carries
    name, phone (when published), and address. Use with care — this is
    PII the school shares between class families.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        html = await _fetch_first(app.client, CONTACTS_PATHS)
    return _stamp(parse_contacts(html, school=app.settings.school))


@mcp.tool()
async def get_library_files(
    ctx: Context[Any, AppContext, Any],
    student_id: int | None = None,
) -> LibraryFileList:
    """List files in the school's shared library / filer & länkar.

    Each :class:`LibraryFile` carries the display title, clean filename,
    optional description, size, and the ``request_id`` you'd pass to
    ``right_student_library_download.jsp?requestid=<n>`` to fetch the
    file. (No dedicated download MCP tool yet; use ``dump_page`` against
    that path when you need the bytes.)
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        html = await _fetch_first(app.client, LIBRARY_PATHS)
    return _stamp(parse_library_files(html, school=app.settings.school))


@mcp.tool()
async def get_grades(
    ctx: Context[Any, AppContext, Any],
    student_id: int | None = None,
) -> GradeList:
    """Return the subject-grade report for the active child.

    Maps to Elevdokument → Betyg in the SchoolSoft UI. Each
    :class:`GradeEntry` is one ``(subject, term)`` pair — there can be
    multiple terms per subject (e.g. ``25/26 Ht``, ``25/26 Vt``).
    Entries with no grade *and* no note are skipped to keep the
    response compact. The ``terms`` field lists all term columns seen,
    in source-page order.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        html = await _fetch_first(app.client, GRADES_PATHS)
    return _stamp(parse_grades(html, school=app.settings.school))


@mcp.tool()
async def get_homework(
    ctx: Context[Any, AppContext, Any],
    week: int | None = None,
    year: int | None = None,
    student_id: int | None = None,
    include_body: bool = True,
    max_body_chars: int = 4000,
) -> HomeworkList:
    """Return assignments / läxor / prov for the given ISO week.

    Each item carries the assignment's full text in ``body`` (what the
    pupil is actually meant to do), plus machine-readable
    ``start_date``/``end_date`` and the teacher's name — the week-scoped
    start-page endpoint alone gives none of those, only a prose subtitle.

    Set ``include_body=False`` to skip one request per assignment.
    """
    app = _app(ctx)
    now = _now_as_of()
    actual_week = week if week is not None else now.iso_week
    actual_year = year if year is not None else now.iso_year
    first, last = sr.week_bounds(actual_year, actual_week)

    async with app.lock:
        await _select_child(app, student_id)
        usertype = app.settings.usertype
        try:
            rows = await app.client.fetch_json(
                sr.path(sr.ASSIGNMENT_ROWS, usertype)
            )
        except _REST_ERRORS as err:
            logger.debug("Assignment grid failed (%s), falling back", err)
            return _stamp(
                await _homework_from_start_page(app, actual_week, actual_year)
            )

        items: list[HomeworkItem] = []
        for entry in rows if isinstance(rows, list) else []:
            if not isinstance(entry, dict):
                continue
            row = sr.parse_assignment_row(entry)
            start = sr.parse_iso_date(row["start_date"])
            end = sr.parse_iso_date(row["end_date"])
            # An assignment with no dates at all can't be placed in a week;
            # dropping it is better than showing it every week forever.
            if start is None and end is None:
                continue
            if not sr.overlaps(start, end, first, last):
                continue
            view: dict[str, Any] = {}
            if include_body and row["assignment_id"] is not None:
                view = await _assignment_view(
                    app, row["assignment_id"], max_body_chars
                )
            items.append(
                HomeworkItem(
                    title=view.get("title") or row["title"],
                    subject=view.get("subject") or row["subject"],
                    kind=view.get("kind") or row["kind"],
                    date_range=view.get("date_range", ""),
                    subtitle=view.get("subtitle", ""),
                    due=end.isoformat() if end else None,
                    read=row["read"],
                    submission_status=row["submission_status"],
                    result_status=row["result_status"],
                    assignment_id=row["assignment_id"],
                    activity_id=row["activity_id"],
                    teacher=row["teacher"],
                    start_date=start.isoformat() if start else None,
                    end_date=end.isoformat() if end else None,
                    publish_date=row["publish_date"],
                    status=row["status"],
                    body=view.get("body", ""),
                    description=view.get("body", "") or row["title"],
                )
            )

    items.sort(key=lambda i: (i.end_date or "", i.subject))
    return _stamp(
        HomeworkList(
            school=app.settings.school,
            items=items,
            week=actual_week,
            year=actual_year,
            note=None
            if items
            else (
                f"No assignments overlap week {actual_week}/{actual_year}. On a "
                "parent account, check that the right child is selected — pass "
                "student_id."
            ),
        )
    )


async def _assignment_view(
    app: AppContext, assignment_id: int, max_body_chars: int | None
) -> dict[str, Any]:
    """Fetch one assignment's description. Caller must hold ``app.lock``."""
    try:
        payload = await app.client.fetch_json(
            sr.path(
                sr.ASSIGNMENT_VIEW, app.settings.usertype, assignment_id=assignment_id
            )
        )
    except _REST_ERRORS as err:
        logger.warning("Assignment %s body unavailable: %s", assignment_id, err)
        return {}
    return sr.parse_detail_view(payload, max_body_chars=max_body_chars)


async def _homework_from_start_page(
    app: AppContext, week: int, year: int
) -> HomeworkList:
    """Legacy path: week-scoped titles only. Caller must hold ``app.lock``."""
    params = {"week": str(week), "year": str(year)}
    try:
        payload = await app.client.fetch_json(HOMEWORK_REST_PATH, params=params)
    except _REST_ERRORS as err:
        logger.debug("Homework REST failed (%s), falling back to JSP", err)
        html = await _fetch_first(app.client, HOMEWORK_PATHS)
        return parse_homework(html, school=app.settings.school)
    result = parse_homework_json(
        payload, school=app.settings.school, week=week, year=year
    )
    result.note = ((result.note + " ") if result.note else "") + (
        "Fetched from the legacy start-page endpoint: no bodies, no ISO dates."
    )
    return result


@mcp.tool()
async def get_planning(
    ctx: Context[Any, AppContext, Any],
    week: int | None = None,
    year: int | None = None,
    student_id: int | None = None,
    include_body: bool = True,
    max_body_chars: int = 4000,
) -> PlanningList:
    """Return lesson plans (planeringar) in force during the given ISO week.

    **This is where "what is Idrott this week and where do they meet?"
    lives.** Each planning carries the teacher's own text in ``body``, and
    ``week_lines`` pulls out the line(s) naming the requested week — a
    term-long planning is in force every school day but only one of its
    lines is about any given week.

    Two sources are merged:

    - ``ps/subjectroom/plannings/grid/rows`` — every planning with real ISO
      ``start_date``/``end_date``, teacher and subject. Filtered here to
      those overlapping the requested week, which keeps long-running term
      plans (the ones carrying the week-by-week detail) instead of dropping
      them the way a week-scoped query does.
    - ``ps/planning_parts/<id>/view`` — the body, fetched per planning when
      ``include_body`` is set.

    Falls back to the legacy week-scoped start-page list if the grid
    endpoint is unavailable; that path returns titles only, and says so in
    ``note``.

    Set ``include_body=False`` for a cheap listing. ``max_body_chars``
    truncates each body (term plans can run to several pages).
    """
    app = _app(ctx)
    now = _now_as_of()
    actual_week = week if week is not None else now.iso_week
    actual_year = year if year is not None else now.iso_year
    first, last = sr.week_bounds(actual_year, actual_week)

    async with app.lock:
        await _select_child(app, student_id)
        usertype = app.settings.usertype
        try:
            rows = await app.client.fetch_json(
                sr.path(sr.PLANNING_ROWS, usertype)
            )
        except _REST_ERRORS as err:
            logger.debug("Planning grid failed (%s), falling back to start-page", err)
            return _stamp(
                await _planning_from_start_page(app, actual_week, actual_year)
            )

        items: list[PlanningPart] = []
        for entry in rows if isinstance(rows, list) else []:
            if not isinstance(entry, dict):
                continue
            row = sr.parse_planning_row(entry)
            if not sr.overlaps(
                sr.parse_iso_date(row["start_date"]),
                sr.parse_iso_date(row["end_date"]),
                first,
                last,
            ):
                continue
            view: dict[str, Any] = {}
            if include_body and row["part_id"] is not None:
                view = await _planning_part_view(
                    app, row["part_id"], actual_week, max_body_chars
                )
            items.append(
                PlanningPart(
                    title=view.get("title") or row["title"],
                    subject=view.get("subject") or row["subject"],
                    kind="Planering",
                    date_range=view.get("date_range", ""),
                    subtitle=view.get("subtitle", ""),
                    read=row["read"],
                    part_id=row["part_id"],
                    planning_id=row["planning_id"],
                    activity_id=row["activity_id"],
                    teacher=row["teacher"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    publish_date=view.get("publish_date") or row["publish_date"],
                    status=row["status"],
                    body=view.get("body", ""),
                    week_lines=view.get("week_lines") or [],
                    mentions_weeks=bool(view.get("mentions_weeks")),
                )
            )

    items.sort(key=lambda i: (i.start_date or "", i.subject))
    return _stamp(
        PlanningList(
            school=app.settings.school,
            items=items,
            week=actual_week,
            year=actual_year,
            note=None
            if items
            else (
                f"No plannings overlap week {actual_week}/{actual_year}. Either "
                "no teacher has published one, or no child is selected — pass "
                "student_id on a parent account."
            ),
        )
    )


async def _fill_planning_bodies(
    app: AppContext,
    items: list[PlanningPart],
    *,
    activity_ids: set[int],
    week: int,
    max_body_chars: int,
    student_id: int | None,
) -> list[PlanningPart]:
    """Fetch bodies for just the plannings a given day needs.

    A child has a planning per subject, in force all term. Fetching every
    body to answer "what does Tuesday need" is one request per subject
    where three would do, and the fifteen that are not on Tuesday's
    schedule are dropped by the join a moment later.
    """
    if not activity_ids:
        return []
    filled: list[PlanningPart] = []
    async with app.lock:
        await _select_child(app, student_id)
        for item in items:
            if item.activity_id not in activity_ids or item.part_id is None:
                continue
            view = await _planning_part_view(
                app, item.part_id, week, max_body_chars
            )
            filled.append(
                item.model_copy(
                    update={
                        "title": view.get("title") or item.title,
                        "subject": view.get("subject") or item.subject,
                        "date_range": view.get("date_range") or item.date_range,
                        "subtitle": view.get("subtitle") or item.subtitle,
                        "publish_date": view.get("publish_date")
                        or item.publish_date,
                        "body": view.get("body", ""),
                        "week_lines": view.get("week_lines") or [],
                        "mentions_weeks": bool(view.get("mentions_weeks")),
                    }
                )
            )
    return filled


async def _planning_part_view(
    app: AppContext, part_id: int, week: int | None, max_body_chars: int | None
) -> dict[str, Any]:
    """Fetch one planning part's body. Caller must hold ``app.lock``.

    A body that won't load must not take the whole listing down with it —
    a planning with a title and no text is still worth showing, and the
    empty ``body`` is itself the signal that something went wrong.
    """
    try:
        payload = await app.client.fetch_json(
            sr.path(sr.PLANNING_PART_VIEW, app.settings.usertype, part_id=part_id)
        )
    except _REST_ERRORS as err:
        logger.warning("Planning part %s body unavailable: %s", part_id, err)
        return {}
    return sr.parse_detail_view(payload, week=week, max_body_chars=max_body_chars)


async def _planning_from_start_page(
    app: AppContext, week: int, year: int
) -> PlanningList:
    """Legacy path: titles and subtitles only. Caller must hold ``app.lock``."""
    payload = await app.client.fetch_json(
        PLANNING_REST_PATH, params={"week": str(week), "year": str(year)}
    )
    result = parse_planning_json(
        payload, school=app.settings.school, week=week, year=year
    )
    result.note = (
        (result.note + " ") if result.note else ""
    ) + (
        "Fetched from the legacy start-page endpoint: titles only, no planning "
        "bodies. The subject-room grid endpoint was unavailable."
    )
    return result


@mcp.tool()
async def get_planning_detail(
    ctx: Context[Any, AppContext, Any],
    part_id: int,
    week: int | None = None,
    student_id: int | None = None,
    max_body_chars: int = 20000,
) -> PlanningDetail:
    """Return one planning in full, including attached files and links.

    ``part_id`` is the ``part_id`` from ``get_planning``. ``week`` selects
    which lines land in ``week_lines`` and **defaults to the current ISO
    week**, so pass it explicitly when asking about any other week. Use this
    when ``get_planning``'s truncated body cut off something you need, or to
    reach the material a teacher attached.

    Dates, teacher and status come from the plannings grid. If that lookup
    fails they are blank and ``note`` says so — blank is not "the teacher
    left them empty".
    """
    app = _app(ctx)
    now = _now_as_of()
    async with app.lock:
        await _select_child(app, student_id)
        usertype = app.settings.usertype
        payload = await app.client.fetch_json(
            sr.path(sr.PLANNING_PART_VIEW, usertype, part_id=part_id)
        )
        view = sr.parse_detail_view(
            payload,
            week=week if week is not None else now.iso_week,
            max_body_chars=max_body_chars,
        )
        row = await _planning_row_for(app, part_id)
        material = await _material_for(app, part_id)

    return _stamp(
        sr.parse_planning_detail(
            view,
            row=row,
            material=material,
            note=None
            if row
            else "Grid metadata (dates, teacher, status) could not be read; "
            "those fields are blank rather than empty.",
        )
    )


async def _planning_row_for(app: AppContext, part_id: int) -> dict[str, Any]:
    """Grid metadata (dates, teacher, status) for one part. Lock must be held."""
    try:
        rows = await app.client.fetch_json(
            sr.path(sr.PLANNING_ROWS, app.settings.usertype)
        )
    except _REST_ERRORS as err:
        logger.debug("Planning grid unavailable for part %s: %s", part_id, err)
        return {}
    for entry in rows if isinstance(rows, list) else []:
        if isinstance(entry, dict) and entry.get("planningPartId") == part_id:
            return sr.parse_planning_row(entry)
    return {}


async def _material_for(app: AppContext, section_id: int) -> list[Any]:
    """Files + links hung on a planning or assignment. Lock must be held."""
    usertype = app.settings.usertype
    files = await _fetch_json_or_none(
        app,
        sr.path(sr.MATERIAL_FILES, usertype, section_id=section_id),
        label="Material files",
    )
    links = await _fetch_json_or_none(
        app,
        sr.path(sr.MATERIAL_LINKS, usertype, section_id=section_id),
        label="Material links",
    )
    return sr.parse_material(files, links)


@mcp.tool()
async def get_subject_rooms(
    ctx: Context[Any, AppContext, Any],
    student_id: int | None = None,
    include_teachers: bool = True,
) -> SubjectRoomList:
    """List the child's subject rooms (ämnesrum) with their teachers.

    Each room's ``activity_id`` is the join key used across the modern
    planning surface: plannings, assignments and schedule lessons all
    carry it. Use this to answer "who teaches Idrott?" or to scope
    ``get_planning`` results to one subject.

    ``include_teachers=False`` skips one request per room.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        usertype = app.settings.usertype
        payload = await app.client.fetch_json(sr.path(sr.ROOMS_ALL, usertype))
        result = sr.parse_rooms(payload, school=app.settings.school)
        if include_teachers:
            for room in result.rooms:
                try:
                    teachers = await app.client.fetch_json(
                        sr.path(
                            sr.ROOM_TEACHERS, usertype, activity_id=room.activity_id
                        )
                    )
                except _REST_ERRORS as err:
                    logger.debug("Teachers for room %s failed: %s", room.activity_id, err)
                    continue
                room.teachers = sr.parse_teachers(teachers)
    return _stamp(result)


@mcp.tool()
async def get_exam_schedule(
    ctx: Context[Any, AppContext, Any],
    student_id: int | None = None,
) -> ExamSchedule:
    """Return announced exams (provschema) for the active child.

    Independent of ``get_homework``'s week window: an exam announced for
    week 39 shows up here in week 37, which is when a family can still do
    something about it.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        payload = await app.client.fetch_json(
            sr.path(sr.EXAM_SCHEDULE, app.settings.usertype)
        )
    return _stamp(sr.parse_exam_schedule(payload, school=app.settings.school))


@mcp.tool()
async def get_lesson_detail(
    ctx: Context[Any, AppContext, Any],
    lesson_id: int,
    student_id: int | None = None,
) -> LessonDetail:
    """Return room, teachers and group for one scheduled lesson.

    ``lesson_id`` is the ``lesson_id`` from ``get_schedule``.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        payload = await app.client.fetch_json(
            sr.path(sr.LESSON_DETAIL, app.settings.usertype, lesson_id=lesson_id)
        )
    teachers = payload.get("teachers") if isinstance(payload, dict) else None
    return _stamp(
        LessonDetail(
            lesson_id=lesson_id,
            title=(payload or {}).get("title", "") or "",
            room=(payload or {}).get("room", "") or "",
            teachers=[
                t.get("name", "")
                for t in (teachers or [])
                if isinstance(t, dict) and t.get("name")
            ],
            groups=(payload or {}).get("groups", "") or "",
        )
    )


@mcp.tool()
async def get_attendance(
    ctx: Context[Any, AppContext, Any],
    student_id: int | None = None,
) -> AttendanceReport:
    """Return per-week attendance statistics for the active child.

    Maps to Frånvaro → Rapport in the SchoolSoft UI. Each ``AttendanceWeek``
    carries total presence percentage, unreported/reported absence counts,
    and the detailed sub-categories (sen ankomst, föranmäld, etc.).
    For the list of *individual* unreported absences instead, call
    ``get_unreported_absence``.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        html = await _fetch_first(app.client, ATTENDANCE_PATHS)
    return _stamp(parse_attendance(html, school=app.settings.school))


@mcp.tool()
async def get_unreported_absence(
    ctx: Context[Any, AppContext, Any],
    student_id: int | None = None,
) -> UnreportedAbsenceList:
    """Return unreported absence, split by whether it has been acknowledged.

    Maps to Frånvaro → Oanmäld frånvaro. SchoolSoft texts a guardian when a
    lesson is missed, and the guardian is then expected to open that page
    and confirm they have seen it.

    ``events`` holds only the absences **still awaiting** that confirmation
    — the ones to act on. Rows a guardian has already confirmed stay on the
    page permanently and are returned separately in ``acknowledged``, with
    who confirmed and when. Reporting the second group as new is a false
    alarm that repeats every morning.

    ``confirmed_none_pending`` distinguishes "the page said there is
    nothing" from "we parsed nothing", which are not the same thing.

    This tool only reads. Confirming an absence is the guardian's own
    attestation that they have seen it, so it is not automated here: a
    robot ticking it means the school's record says a parent took note
    when nobody did.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        html = await _fetch_first(app.client, UNREPORTED_ABSENCE_PATHS)
    return _stamp(parse_unreported_absence(html, school=app.settings.school))


@mcp.tool()
async def get_news(
    ctx: Context[Any, AppContext, Any],
    older: bool = False,
    student_id: int | None = None,
) -> NewsFeed:
    """Return news items including 'veckobrev'. Set ``older=True`` for archived items.

    Each item carries a ``news_id`` and ``type_id`` you can pass to
    ``get_news_item`` for the full body + attachments, or use the attachment
    ``fileid`` directly with ``download_attachment`` / ``read_attachment_text``.

    The feed is per-child. On a multi-child parent account pass
    ``student_id`` (from ``list_children``) to read a specific child's news —
    and pass the *same* ``student_id`` when downloading its attachments,
    since SchoolSoft only serves files for the selected child.
    """
    app = _app(ctx)
    params = {"type": "2"} if older else None
    async with app.lock:
        await _select_child(app, student_id)
        html = await _fetch_first(app.client, NEWS_PATHS, params=params)
    return _stamp(
        parse_news(
            html,
            school=app.settings.school,
            default_type_id=2 if older else 1,
        )
    )


@mcp.tool()
async def get_news_item(
    ctx: Context[Any, AppContext, Any],
    news_id: int,
    type_id: int = 1,
    student_id: int | None = None,
) -> NewsItem:
    """Fetch one news item with the full body and attachments.

    ``news_id`` comes from get_news().items[*].news_id. ``type_id`` is 1 for
    current items, 2 for older — usually matches the item you got from get_news.
    ``student_id`` selects the child the item belongs to (see ``get_news``).
    """
    app = _app(ctx)
    params = {
        "requestid": str(news_id),
        "type": str(type_id),
        "action": "view",
    }
    async with app.lock:
        await _select_child(app, student_id)
        html = await app.client.fetch_html(NEWS_ITEM_PATH, params=params)
    feed = parse_news(html, school=app.settings.school, default_type_id=type_id)
    for item in feed.items:
        if item.news_id == news_id:
            return item
    # Detail view didn't yield the expected item — return whatever we got
    # so the caller can still see the (possibly truncated) page contents.
    if feed.items:
        return feed.items[0]
    return NewsItem(
        news_id=news_id,
        type_id=type_id,
        body="",
        date="",
    )


@mcp.tool()
async def download_attachment(
    ctx: Context[Any, AppContext, Any],
    news_id: int,
    fileid: int,
    type_id: int = 1,
    object_kind: str = "news",
    student_id: int | None = None,
    max_bytes: int = 700_000,
) -> AttachmentBytes:
    """Download a news/message attachment as base64-encoded bytes.

    **For reading content, use ``read_attachment_text`` instead** — it
    extracts plain text from PDF / .docx and is far cheaper for LLM
    context. This tool returns raw bytes (base64) and is only useful when
    you need the actual file (e.g. to forward it to disk-write tooling).

    Refuses files larger than ``max_bytes`` (default 700 KB raw, ≈ 950 KB
    base64) so the tool result stays under typical 1 MB MCP limits. On
    refusal, returns an empty ``data_base64`` with the real ``size_bytes``
    and a note suggesting ``read_attachment_text`` — the caller can then
    extract text instead.

    ``object_kind`` is ``"news"`` for veckobrev / news attachments and
    ``"message"`` for inbox messages. ``type_id`` matches the news item's
    type field (1 = current, 2 = older).

    On a multi-child parent account, pass the ``student_id`` of the child
    the item belongs to — SchoolSoft only serves attachments for the child
    currently selected in the session, and answers 404 for the others.
    """
    if object_kind not in {"news", "message"}:
        raise ValueError("object_kind must be 'news' or 'message'")
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        content, headers, note = await _fetch_attachment(
            app,
            news_id=news_id,
            fileid=fileid,
            type_id=type_id,
            object_kind=object_kind,
        )
    fallback_name = f"attachment_{fileid}"
    filename = filename_from_headers(headers, fallback_name)
    content_type = headers.get("content-type", guess_content_type(filename)).split(";")[0].strip()

    if note is not None:
        return AttachmentBytes(
            filename=filename,
            content_type=content_type,
            size_bytes=0,
            data_base64="",
            note=note,
        )

    if len(content) > max_bytes:
        return AttachmentBytes(
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            data_base64="",
            note=(
                f"File is {len(content):,} bytes — exceeds max_bytes={max_bytes:,} "
                "to keep the tool result under the 1 MB MCP cap. Use "
                "read_attachment_text to get extracted plain text instead, "
                "or call again with a larger max_bytes if you really need raw bytes."
            ),
        )

    return AttachmentBytes(
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        data_base64=base64.b64encode(content).decode("ascii"),
    )


@mcp.tool()
async def read_attachment_text(
    ctx: Context[Any, AppContext, Any],
    news_id: int,
    fileid: int,
    type_id: int = 1,
    object_kind: str = "news",
    student_id: int | None = None,
    max_chars: int = 50_000,
    offset: int = 0,
) -> AttachmentText:
    """Download an attachment and return its extracted plain text.

    Supports PDF (pypdf), .docx (python-docx), and plain text. For other
    content types the ``note`` explains what to do; use ``download_attachment``
    when you need raw bytes.

    This is the right tool for "vad står det i veckobrevet?" — far cheaper
    than passing raw base64 bytes to the LLM.

    For long documents that won't fit in one response, call again with
    ``offset`` advanced past the chars already received. The returned
    ``truncated=True`` and the response's ``next_offset`` field signal more
    content is available.

    On a multi-child parent account, pass the ``student_id`` of the child
    the item belongs to — SchoolSoft only serves attachments for the child
    currently selected in the session, and answers 404 for the others.
    """
    if object_kind not in {"news", "message"}:
        raise ValueError("object_kind must be 'news' or 'message'")
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        content, headers, failure = await _fetch_attachment(
            app,
            news_id=news_id,
            fileid=fileid,
            type_id=type_id,
            object_kind=object_kind,
        )
    fallback_name = f"attachment_{fileid}"
    filename = filename_from_headers(headers, fallback_name)
    content_type = headers.get("content-type", guess_content_type(filename)).split(";")[0].strip()

    if failure is not None:
        return AttachmentText(
            filename=filename,
            content_type=content_type,
            size_bytes=0,
            text="",
            note=failure,
        )

    # Extract enough to satisfy offset + max_chars, then slice. Keeps the
    # parser simple (no per-format streaming) while still letting callers
    # paginate through large documents.
    text, truncated, note = extract_text(
        content, content_type, limit=offset + max_chars
    )
    if offset > 0:
        text = text[offset:]
        if len(text) >= max_chars:
            truncated = True
    return AttachmentText(
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        text=text,
        truncated=truncated,
        next_offset=offset + len(text) if truncated else None,
        note=note,
    )


@mcp.tool()
async def get_messages(
    ctx: Context[Any, AppContext, Any],
    student_id: int | None = None,
) -> MessageList:
    """Return inbox messages (EXPERIMENTAL)."""
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        html = await _fetch_first(app.client, MESSAGES_PATHS)
    return _stamp(parse_messages(html, school=app.settings.school))


def _briefing_note(lessons: list[DayLesson], *, schedule_failed: bool) -> str | None:
    """The one-line explanation for a day with no lessons on it.

    An empty schedule means one of two very different things, and saying
    "holiday" when the fetch simply failed sends a family to school on the
    wrong day, or keeps them home on the right one.
    """
    if lessons:
        return None
    if schedule_failed:
        return (
            "The schedule could not be loaded, so this day looks empty. See "
            "``errors`` — this is NOT a statement that there is no school."
        )
    return (
        "No lessons on this date — a holiday, a study day, or the "
        "schedule has not been published for this week."
    )


@mcp.tool()
async def get_day_briefing(
    ctx: Context[Any, AppContext, Any],
    date: str | None = None,
    student_id: int | None = None,
    news_days: int = 14,
    max_body_chars: int = 2000,
) -> DayBriefing:
    """Everything about one school day for one child, joined in a single call.

    Pass ``date`` as ISO ``YYYY-MM-DD`` (defaults to today). Returns the
    day's lessons **with the planning text that applies to that week
    attached to each lesson**, assignments due today and within the week,
    announced exams, unreported absence, and news/veckobrev from the last
    ``news_days`` days.

    Prefer this over calling ``get_schedule`` + ``get_planning`` +
    ``get_homework`` separately when the question is "what does this child
    need today". The joining is the whole point: a schedule row says
    "Idrott 08:20", and only the subject's planning says the class is
    meeting at the disc-golf course rather than at school. Reading them
    apart is how that detail goes missing.

    ``prepare`` is the short list a parent acts on before the child leaves:
    lessons that need kit or a different meeting point, exams, and work due.
    Every entry is derived from fetched data — when a preparation-heavy
    lesson has no published planning, it says so rather than guessing.

    A section that fails to load is reported in ``errors``; the rest of the
    briefing is still returned.
    """
    app = _app(ctx)
    try:
        day = dt.date.fromisoformat(date) if date else dt.date.today()
    except ValueError as err:
        raise ValueError(
            f"date must be ISO YYYY-MM-DD, got {date!r}"
        ) from err
    iso_year, iso_week, iso_weekday = day.isocalendar()
    day_key = sr.DAY_KEYS[iso_weekday - 1]
    errors: list[str] = []

    student_name = ""
    known_ids: list[int] = []
    try:
        children = await list_children(ctx)
        known_ids = [c.student_id for c in children.children if c.student_id]
        if student_id is None:
            match = next((c for c in children.children if c.active), None)
        else:
            match = next(
                (c for c in children.children if c.student_id == student_id), None
            )
        if match is not None:
            student_name = match.name
            if student_id is None:
                student_id = match.student_id
    except Exception as err:  # a name is a nicety, not the payload
        errors.append(f"list_children: {type(err).__name__}: {err}")

    # Fail loudly on an id that is not on this account. Every section below
    # would raise anyway, and the briefing would come back with six opaque
    # errors and a note announcing a holiday.
    if student_id is not None and known_ids and student_id not in known_ids:
        raise ValueError(
            f"student_id {student_id} is not on this account. "
            f"Known ids: {', '.join(str(i) for i in known_ids)}. "
            "Call list_children() for names."
        )

    async def _section(label: str, coro: Any, fallback: Any) -> Any:
        try:
            return await coro
        except Exception as err:  # partial day beats no day
            logger.warning("Briefing section %s failed: %s", label, err)
            errors.append(f"{label}: {type(err).__name__}: {err}")
            return fallback

    schedule = await _section(
        "schedule",
        get_schedule(ctx, week=iso_week, year=iso_year, student_id=student_id),
        None,
    )
    # Grid first, bodies second: which bodies are worth a request is not
    # known until the day's lessons have been joined to the subjects.
    plannings = await _section(
        "planning",
        get_planning(
            ctx,
            week=iso_week,
            year=iso_year,
            student_id=student_id,
            include_body=False,
        ),
        None,
    )
    homework = await _section(
        "homework",
        get_homework(
            ctx,
            week=iso_week,
            year=iso_year,
            student_id=student_id,
            include_body=True,
            max_body_chars=max_body_chars,
        ),
        None,
    )
    exams = await _section("exams", get_exam_schedule(ctx, student_id=student_id), None)
    absence = await _section(
        "unreported_absence", get_unreported_absence(ctx, student_id=student_id), None
    )
    news = await _section("news", get_news(ctx, student_id=student_id), None)

    # --- lessons on the day, with their subject's planning attached ---------
    all_plannings = list(getattr(plannings, "items", []) or [])
    day_schedule = [
        le
        for le in (getattr(schedule, "lessons", []) or [])
        if (le.day or "").lower() == day_key
    ]
    wanted = {
        sr.activity_for_lesson(lesson, all_plannings)
        for lesson in day_schedule
        if not lesson.is_break
    }
    wanted.discard(-1)
    planning_items = await _section(
        "planning bodies",
        _fill_planning_bodies(
            app,
            all_plannings,
            activity_ids=wanted,
            week=iso_week,
            max_body_chars=max_body_chars,
            student_id=student_id,
        ),
        [],
    )
    lessons = sr.day_lessons(day_schedule, planning_items, day_key)

    # --- what needs doing before leaving the house --------------------------
    prepare = sr.preparation_notes(lessons, week=iso_week)

    # --- assignments -------------------------------------------------------
    due_today: list[HomeworkItem] = []
    due_soon: list[HomeworkItem] = []
    horizon = day + dt.timedelta(days=7)
    for item in getattr(homework, "items", []) or []:
        end = sr.parse_iso_date(item.end_date or item.due)
        if end is None:
            continue
        if end == day:
            due_today.append(item)
        elif day < end <= horizon:
            due_soon.append(item)
    tomorrow = day + dt.timedelta(days=1)
    for item in due_today:
        prepare.append(
            f"Ska vara klart idag: {item.title}"
            + (f" ({item.subject})" if item.subject else "")
        )
    for item in due_soon:
        if sr.parse_iso_date(item.end_date or item.due) == tomorrow:
            prepare.append(
                f"Ska vara klart imorgon: {item.title}"
                + (f" ({item.subject})" if item.subject else "")
            )

    # --- announced exams still inside their visibility window ---------------
    # Deliberately *not* fed into ``prepare``: the exam schedule's dates are
    # the window the announcement is shown in, not when the exam is written.
    # "Prov fredag v. 39" is listed from 7 Sep, and treating that as the exam
    # date would raise the alarm two weeks early, every day. The real date is
    # on the matching assignment, which reaches ``prepare`` via due_today.
    upcoming_exams = []
    for exam in getattr(exams, "exams", []) or []:
        start = sr.parse_iso_date(exam.start)
        end = sr.parse_iso_date(exam.end)
        if start is not None and start > day + dt.timedelta(days=30):
            continue
        if end is not None and end < day:
            continue
        upcoming_exams.append(exam)

    # --- recent news --------------------------------------------------------
    cutoff = day - dt.timedelta(days=news_days)
    recent_news = []
    for item in getattr(news, "items", []) or []:
        published = sr.parse_loose_date(item.published or item.date, near=day)
        if published is None or published >= cutoff:
            recent_news.append(item)

    return _stamp(
        DayBriefing(
            school=app.settings.school,
            date=day.isoformat(),
            weekday=sr.DAY_NAMES_SV.get(day_key, day_key),
            iso_week=iso_week,
            iso_year=iso_year,
            student_id=student_id,
            student_name=student_name,
            is_school_day=any(not lesson.is_break for lesson in lessons),
            lessons=lessons,
            prepare=prepare,
            due_today=due_today,
            due_soon=due_soon,
            plannings=[
                # Field-for-field the same model minus ``kind``/``subtitle``.
                # Rebuilding it by hand is how ``material`` went missing.
                PlanningDetail(
                    **item.model_dump(exclude={"kind", "subtitle"})
                )
                for item in planning_items
            ],
            exams=upcoming_exams,
            unreported_absence=list(getattr(absence, "events", []) or []),
            news=recent_news,
            errors=errors,
            note=_briefing_note(lessons, schedule_failed=schedule is None),
        )
    )


@mcp.tool()
async def get_assessments(
    ctx: Context[Any, AppContext, Any],
    student_id: int | None = None,
) -> AssessmentList:
    """Return Sammantagen bedömning — one row per subject, warnings first.

    This is what a Swedish school publishes for the years that carry no
    formal grades. On those years ``get_grades`` (Betyg) is close to empty
    while this holds everything the teachers have said, so prefer this one
    for anything below the grading years.

    Each subject carries the school's wording ("Godtagbara kunskaper", "Mer
    än godtagbara kunskaper"), when it was last updated and published, and
    whether a guardian has read it.

    ``subject_warning`` is the school's flag that a subject risks not
    reaching the goals. Flagged subjects sort first and are repeated in
    ``warnings``. An empty ``warnings`` is worth saying out loud rather than
    omitting — "no subject is flagged" is an answer.

    Call ``get_assessment_detail`` for the teacher's own text, the graded
    work behind the assessment, and a warning's motivation.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        usertype = app.settings.usertype
        rows = await app.client.fetch_json(
            asm.path_for(asm.ASSESSMENT_ROWS, usertype)
        )
        options = await _fetch_json_or_none(
            app,
            asm.path_for(asm.ASSESSMENT_OPTIONS, usertype),
            label="Assessment options",
        )

    subjects = [
        SubjectAssessment(**row) for row in asm.parse_assessment_rows(rows, options)
    ]
    warnings = [s.subject for s in subjects if s.subject_warning]
    # A subject the school has not assessed yet is not something a guardian
    # has failed to read. Counting it as unread manufactures 21 items of
    # homework for a parent who has nothing to do.
    published = [s for s in subjects if s.published]
    unread = sum(1 for s in published if not s.read)
    note = None
    if not subjects:
        note = (
            "No holistic assessment published for this child. Some school "
            "years use Betyg instead — try get_grades()."
        )
    elif not published:
        note = (
            f"None of the {len(subjects)} subjects has a published assessment "
            "yet. This school year probably uses Betyg — try get_grades()."
        )
    elif warnings:
        note = (
            f"{len(warnings)} subject(s) flagged as at risk of not reaching "
            f"the goals: {', '.join(warnings)}. Call get_assessment_detail "
            "for the motivation before repeating this to a family."
        )
    return _stamp(
        AssessmentList(
            school=app.settings.school,
            student_id=student_id,
            subjects=subjects,
            warnings=warnings,
            unread=unread,
            note=note,
        )
    )


@mcp.tool()
async def get_assessment_detail(
    ctx: Context[Any, AppContext, Any],
    assessment_id: int,
    student_id: int | None = None,
) -> AssessmentDetail:
    """Return one subject's assessment in full.

    ``assessment_id`` is the ``assessment_id`` from ``get_assessments``.
    Returns the knowledge-development wording, any support measures the
    school has put in place, the teacher's formative comments, the graded
    work behind the assessment (``assessed_work`` — this is where an actual
    grade like "B" lives, not on ``get_results``), the subject warning with
    its motivation, and the earlier terms the same subject was assessed in.

    ``published_sections`` says which sections this school publishes to
    guardians. A section missing from it is not withheld by this tool; the
    school does not publish it.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        usertype = app.settings.usertype

        def path(template: str) -> str:
            return asm.path_for(template, usertype, assessment_id=assessment_id)

        header = await app.client.fetch_json(path(asm.ASSESSMENT_HEADER))
        knowledge = await _fetch_json_or_none(
            app, path(asm.ASSESSMENT_KNOWLEDGE), label="Knowledge development"
        )
        warning = await _fetch_json_or_none(
            app, path(asm.ASSESSMENT_WARNING), label="Subject warning"
        )
        work = await _fetch_json_or_none(
            app, path(asm.ASSESSMENT_WORK), label="Assessed work"
        )
        comments = await _fetch_json_or_none(
            app, path(asm.ASSESSMENT_COMMENTS), label="Formative comments"
        )
        sections = await _fetch_json_or_none(
            app, path(asm.ASSESSMENT_SECTIONS), label="Published sections"
        )
        history = await _fetch_json_or_none(
            app, path(asm.ASSESSMENT_HISTORY), label="Assessment history"
        )

    header = header if isinstance(header, dict) else {}
    know = asm.parse_knowledge_development(knowledge)
    warn = asm.parse_subject_warning(warning)
    return _stamp(
        AssessmentDetail(
            school=app.settings.school,
            assessment_id=assessment_id,
            student_name=str(header.get("studentName") or ""),
            subject=str(header.get("activityName") or ""),
            group=str(header.get("groupName") or ""),
            publish_status=str(header.get("publishStatus") or ""),
            assessment=know["assessment"],
            support_measures=know["support_measures"],
            updated_by=know["updated_by"],
            subject_warning=bool(warn["active"]),
            warning_published=bool(warn["published"]),
            warning_comment=str(warn["comment"]),
            comments=asm.parse_comments(comments),
            assessed_work=[AssessedWork(**w) for w in asm.parse_assessed_work(work)],
            published_sections=[
                str(x) for x in (sections if isinstance(sections, list) else [])
            ],
            terms=[AssessmentTerm(**t) for t in asm.parse_history(history)],
            note=(
                "The subject is flagged as at risk, but the warning is not "
                "published to guardians yet — the school is still writing it. "
                "Do not report it as something the family has been told."
                if warn["active"] and not warn["published"]
                else None
            ),
        )
    )


@mcp.tool()
async def get_results(
    ctx: Context[Any, AppContext, Any],
    student_id: int | None = None,
) -> ResultList:
    """Return published results (Ämnen → Resultat), newest first.

    Says *that* a result was published, for which assignment, by which
    teacher and when. It does **not** carry the grade — SchoolSoft's results
    list has no such field. The grade sits on the subject's assessment:
    ``get_assessment_detail(...).assessed_work[].grade``.

    Use this to spot what is new since a family last looked; use
    ``get_assessment_detail`` to find out what the result actually was.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        rows = await app.client.fetch_json(
            asm.path_for(asm.RESULT_ROWS, app.settings.usertype)
        )
    results = [ResultEntry(**r) for r in asm.parse_result_rows(rows)]
    unread = sum(1 for r in results if not r.read)
    return _stamp(
        ResultList(
            school=app.settings.school,
            student_id=student_id,
            results=results,
            unread=unread,
            note=(
                "Results carry no grade on this endpoint. For the grade, call "
                "get_assessment_detail for the subject and read assessed_work."
                if results
                else "No published results."
            ),
        )
    )


@mcp.tool()
async def get_open_work(
    ctx: Context[Any, AppContext, Any],
    student_id: int | None = None,
    include_expired: bool = False,
    entity_type: str | None = None,
) -> OpenWorkList:
    """Return everything currently open, in due order, regardless of week.

    ``get_homework`` answers "what falls in week N", which is the right
    question for a day briefing and the wrong one for "what does this child
    still owe". A task due in three weeks is invisible to the week query and
    present here — this is the UI's *Aktuella* tab.

    The list mixes assignments and plannings; ``entity_type`` on each item
    says which, and the parameter of the same name filters
    (``"ASSIGNMENT"`` for work with a deadline). Term-long plannings appear
    with an end date in December, which is correct and rarely what you want
    when asking what is due.

    Expired work is left out by default. Set ``include_expired`` to see it —
    an expired item is past its date, which for an assignment may mean
    overdue rather than done.
    """
    app = _app(ctx)
    async with app.lock:
        await _select_child(app, student_id)
        rows = await app.client.fetch_json(
            asm.path_for(asm.OPEN_WORK_ROWS, app.settings.usertype)
        )
    parsed = asm.parse_open_work(rows)
    hidden = 0
    if not include_expired:
        keep = [r for r in parsed if r["status"] == "ONGOING"]
        hidden = len(parsed) - len(keep)
        parsed = keep
    if entity_type:
        parsed = [r for r in parsed if r["entity_type"] == entity_type.upper()]
    items = [OpenWorkItem(**r) for r in parsed]

    kinds = Counter(i.entity_type for i in items)
    parts = [f"{count} {name.lower()}(s)" for name, count in sorted(kinds.items())]
    note = ", ".join(parts) if parts else "Nothing open."
    if hidden:
        note += (
            f". {hidden} expired or finished item(s) hidden; pass "
            "include_expired=True to see them."
        )
    return _stamp(
        OpenWorkList(
            school=app.settings.school,
            student_id=student_id,
            items=items,
            note=note,
        )
    )


@mcp.tool()
async def dump_page(
    ctx: Context[Any, AppContext, Any],
    path: str,
    max_bytes: int = 50_000,
) -> str:
    """Fetch the raw HTML of a SchoolSoft path (for debugging parsers).

    The path is relative to the school root, e.g.
    ``jsp/student/right_student_homework.jsp``. Output is truncated to
    ``max_bytes`` characters. Strip personal data before sharing publicly.
    """
    app = _app(ctx)
    async with app.lock:
        html = await app.client.fetch_html(path)
    if len(html) > max_bytes:
        return html[:max_bytes] + f"\n\n... [truncated, total {len(html)} chars]"
    return html


def run() -> None:
    """Entry point: validate config and run the MCP server over stdio."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        Settings.from_env()
    except ConfigError as err:
        raise SystemExit(f"Configuration error: {err}") from err

    mcp.run()
