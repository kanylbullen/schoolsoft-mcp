"""FastMCP server exposing SchoolSoft data as tools."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

import httpx
from mcp.server.fastmcp import Context, FastMCP

from .client import SchoolSoftAuthError, SchoolSoftClient, SchoolSoftConnectionError
from .config import ConfigError, Settings
from .models import (
    AsOf,
    AttachmentBytes,
    AttachmentText,
    AttendanceReport,
    ChildList,
    ContactList,
    GradeList,
    HomeworkList,
    LibraryFileList,
    LunchWeek,
    MessageList,
    NewsFeed,
    NewsItem,
    PlanningList,
    ScheduleWeek,
    SchoolInformation,
    UnreportedAbsenceList,
)
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
    """
    app = _app(ctx)
    async with app.lock:
        payload = await app.client.fetch_json("rest-api/parent/header/parent")
    return _stamp(parse_parent_header(payload, school=app.settings.school))


@mcp.tool()
async def set_active_child(
    ctx: Context[Any, AppContext, Any],
    student_id: int,
    org_id: int = 1,
) -> ChildList:
    """Make ``student_id`` the active child in SchoolSoft's session.

    Subsequent calls to schedule / lunch / homework / planning return data
    for this child until ``set_active_child`` is called again or the session
    expires. ``org_id`` is the school org ID — almost always 1 for a single-
    school account. Returns the refreshed child list (with the new active
    flag) so the caller can confirm the switch.
    """
    app = _app(ctx)
    async with app.lock:
        await app.client.fetch_json(
            "rest-api/parent/header/parent",
            method="PUT",
            params={"childId": str(student_id), "orgId": str(org_id)},
        )
        payload = await app.client.fetch_json("rest-api/parent/header/parent")
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
) -> LunchWeek:
    """Return the lunch menu for the given ISO week (defaults to current week).

    Uses SchoolSoft's modern JSON REST endpoint when available and falls
    back to the legacy JSP page if the REST call fails (older installs).
    """
    app = _app(ctx)
    actual_week = week if week is not None else _now_as_of().iso_week
    rest_path = LUNCH_REST_PATH_TEMPLATE.format(week=actual_week)

    async with app.lock:
        try:
            payload = await app.client.fetch_json(rest_path)
        except (
            SchoolSoftConnectionError,
            SchoolSoftAuthError,
            httpx.HTTPStatusError,
        ) as err:
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
        try:
            lessons_payload = await app.client.fetch_json(lessons_path)
        except (
            SchoolSoftConnectionError,
            SchoolSoftAuthError,
            httpx.HTTPStatusError,
        ) as err:
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
) -> SchoolInformation:
    """Return the Skolinformation page as plain text.

    The page is free-form CMS-edited HTML (school hours, phone numbers,
    term dates, addresses, …), so we don't try to impose structure —
    just extract the visible text and let the caller interpret it.
    """
    app = _app(ctx)
    async with app.lock:
        html = await _fetch_first(app.client, SCHOOL_INFO_PATHS)
    return _stamp(parse_school_info(html, school=app.settings.school))


@mcp.tool()
async def get_contacts(ctx: Context[Any, AppContext, Any]) -> ContactList:
    """Return the class contact list for the active child.

    Maps to Skolinfo → Kontaktlistor. Each :class:`Contact` carries
    name, phone (when published), and address. Use with care — this is
    PII the school shares between class families.
    """
    app = _app(ctx)
    async with app.lock:
        html = await _fetch_first(app.client, CONTACTS_PATHS)
    return _stamp(parse_contacts(html, school=app.settings.school))


@mcp.tool()
async def get_library_files(
    ctx: Context[Any, AppContext, Any],
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
        html = await _fetch_first(app.client, LIBRARY_PATHS)
    return _stamp(parse_library_files(html, school=app.settings.school))


@mcp.tool()
async def get_grades(ctx: Context[Any, AppContext, Any]) -> GradeList:
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
        html = await _fetch_first(app.client, GRADES_PATHS)
    return _stamp(parse_grades(html, school=app.settings.school))


@mcp.tool()
async def get_homework(
    ctx: Context[Any, AppContext, Any],
    week: int | None = None,
    year: int | None = None,
) -> HomeworkList:
    """Return assignments / läxor for the given ISO week (defaults to current).

    Uses SchoolSoft's REST endpoint when available and falls back to the
    legacy JSP page if REST returns a non-2xx. Each item carries the raw
    subtitle plus the parsed-out subject/kind/date_range/due.
    """
    app = _app(ctx)
    now = _now_as_of()
    actual_week = week if week is not None else now.iso_week
    actual_year = year if year is not None else now.iso_year
    params = {"week": str(actual_week), "year": str(actual_year)}

    async with app.lock:
        try:
            payload = await app.client.fetch_json(HOMEWORK_REST_PATH, params=params)
        except (
            SchoolSoftConnectionError,
            SchoolSoftAuthError,
            httpx.HTTPStatusError,
        ) as err:
            logger.debug("Homework REST failed (%s), falling back to JSP", err)
            html = await _fetch_first(app.client, HOMEWORK_PATHS)
            return _stamp(parse_homework(html, school=app.settings.school))

    return _stamp(
        parse_homework_json(
            payload, school=app.settings.school, week=actual_week, year=actual_year
        )
    )


@mcp.tool()
async def get_planning(
    ctx: Context[Any, AppContext, Any],
    week: int | None = None,
    year: int | None = None,
) -> PlanningList:
    """Return lesson plans (planeringar) for the given ISO week.

    Each ``PlanningPart`` is one piece of a larger planning block (a
    teacher's plan for a course over a date range). The subtitle is
    parsed into subject + kind + date_range, with the raw text kept too.
    """
    app = _app(ctx)
    now = _now_as_of()
    actual_week = week if week is not None else now.iso_week
    actual_year = year if year is not None else now.iso_year
    params = {"week": str(actual_week), "year": str(actual_year)}

    async with app.lock:
        payload = await app.client.fetch_json(PLANNING_REST_PATH, params=params)
    return _stamp(
        parse_planning_json(
            payload, school=app.settings.school, week=actual_week, year=actual_year
        )
    )


@mcp.tool()
async def get_attendance(ctx: Context[Any, AppContext, Any]) -> AttendanceReport:
    """Return per-week attendance statistics for the active child.

    Maps to Frånvaro → Rapport in the SchoolSoft UI. Each ``AttendanceWeek``
    carries total presence percentage, unreported/reported absence counts,
    and the detailed sub-categories (sen ankomst, föranmäld, etc.).
    For the list of *individual* unreported absences instead, call
    ``get_unreported_absence``.
    """
    app = _app(ctx)
    async with app.lock:
        html = await _fetch_first(app.client, ATTENDANCE_PATHS)
    return _stamp(parse_attendance(html, school=app.settings.school))


@mcp.tool()
async def get_unreported_absence(
    ctx: Context[Any, AppContext, Any],
) -> UnreportedAbsenceList:
    """Return unreported-absence events for the active child.

    Maps to Frånvaro → Oanmäld frånvaro. Each event has the week, weekday,
    lesson (time + subject) and a school-side status message
    (e.g. "SMS skickades", "Korrigerad anmälan"). These are the rows that
    typically need a parent to file an absence report.
    """
    app = _app(ctx)
    async with app.lock:
        html = await _fetch_first(app.client, UNREPORTED_ABSENCE_PATHS)
    return _stamp(parse_unreported_absence(html, school=app.settings.school))


@mcp.tool()
async def get_news(
    ctx: Context[Any, AppContext, Any],
    older: bool = False,
) -> NewsFeed:
    """Return news items including 'veckobrev'. Set ``older=True`` for archived items.

    Each item carries a ``news_id`` and ``type_id`` you can pass to
    ``get_news_item`` for the full body + attachments, or use the attachment
    ``fileid`` directly with ``download_attachment`` / ``read_attachment_text``.
    """
    app = _app(ctx)
    params = {"type": "2"} if older else None
    async with app.lock:
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
) -> NewsItem:
    """Fetch one news item with the full body and attachments.

    ``news_id`` comes from get_news().items[*].news_id. ``type_id`` is 1 for
    current items, 2 for older — usually matches the item you got from get_news.
    """
    app = _app(ctx)
    params = {
        "requestid": str(news_id),
        "type": str(type_id),
        "action": "view",
    }
    async with app.lock:
        html = await app.client.fetch_html(
            "jsp/student/right_student_news.jsp", params=params
        )
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
    """
    if object_kind not in {"news", "message"}:
        raise ValueError("object_kind must be 'news' or 'message'")
    app = _app(ctx)
    path, params = build_download_path(
        parent_id=news_id, type_id=type_id, fileid=fileid, object_kind=object_kind
    )
    async with app.lock:
        content, headers = await app.client.fetch_bytes(path, params=params)
    fallback_name = f"attachment_{fileid}"
    filename = filename_from_headers(headers, fallback_name)
    content_type = headers.get("content-type", guess_content_type(filename)).split(";")[0].strip()

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
    """
    if object_kind not in {"news", "message"}:
        raise ValueError("object_kind must be 'news' or 'message'")
    app = _app(ctx)
    path, params = build_download_path(
        parent_id=news_id, type_id=type_id, fileid=fileid, object_kind=object_kind
    )
    async with app.lock:
        content, headers = await app.client.fetch_bytes(path, params=params)
    fallback_name = f"attachment_{fileid}"
    filename = filename_from_headers(headers, fallback_name)
    content_type = headers.get("content-type", guess_content_type(filename)).split(";")[0].strip()
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
async def get_messages(ctx: Context[Any, AppContext, Any]) -> MessageList:
    """Return inbox messages (EXPERIMENTAL)."""
    app = _app(ctx)
    async with app.lock:
        html = await _fetch_first(app.client, MESSAGES_PATHS)
    return _stamp(parse_messages(html, school=app.settings.school))


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
