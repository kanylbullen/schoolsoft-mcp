"""Shared pydantic models returned by the MCP tools."""

from __future__ import annotations

from pydantic import BaseModel, Field

WEEKDAYS: tuple[str, ...] = ("monday", "tuesday", "wednesday", "thursday", "friday")


class AsOf(BaseModel):
    """When the response was assembled.

    Lets the model anchor temporal reasoning ("the news item is dated week
    20, today is week 21, so 'next week' in the body means *now*") without
    having to look up the current date out-of-band.
    """

    date: str = Field(description='ISO date, e.g. "2026-05-19".')
    iso_year: int
    iso_week: int


class Child(BaseModel):
    """One of the children attached to a parent account."""

    student_id: int
    name: str = ""
    school: str = Field(default="", description="School/class label shown in the UI.")
    grade: str = Field(default="", description='e.g. "6" for "Läraskolan 6".')
    org_id: int | None = Field(
        default=None,
        description=(
            "SchoolSoft organisation ID for this child's school. Required "
            "alongside student_id when switching the active child — it is "
            "school-specific, not always 1."
        ),
    )
    active: bool = Field(
        default=False,
        description="True if this child is currently selected in SchoolSoft's session.",
    )


class ChildList(BaseModel):
    school: str
    children: list[Child]
    active_student_id: int | None = None
    active_org_id: int | None = None
    note: str | None = None
    as_of: AsOf | None = None


class LunchDay(BaseModel):
    day: str
    meal: str = ""


class LunchWeek(BaseModel):
    week: int
    year: int
    school: str
    days: list[LunchDay]
    as_of: AsOf | None = None

    def as_text(self) -> str:
        lines = [f"Lunch week {self.week} ({self.year}) — {self.school}"]
        for d in self.days:
            lines.append(f"  {d.day.capitalize():<10} {d.meal or '—'}")
        return "\n".join(lines)


class Lesson(BaseModel):
    day: str = Field(description="Weekday name in lowercase English, e.g. 'monday'.")
    start: str = Field(description="HH:MM 24h start time")
    end: str = Field(description="HH:MM 24h end time")
    subject: str = Field(default="", description="Subject code or short name, e.g. 'SV', 'MA', 'Eng'.")
    teacher: str = ""
    room: str = Field(default="", description="e.g. 'Klassrum år 5', 'BCJF', 'slöjdsal'.")
    notes: str = Field(
        default="",
        description="Free-text annotations from SchoolSoft (e.g. 'Diagnos', 'Ombyte').",
    )
    is_break: bool = Field(
        default=False,
        description="True for non-academic entries like 'Rast', 'Lunch', 'promenad', 'Lunchvakt'.",
    )
    lesson_id: int | None = Field(
        default=None,
        description="SchoolSoft event/lesson ID. Useful for cross-referencing absence reports.",
    )
    teaching_group: str = Field(
        default="",
        description='Group label, e.g. "6", "5,4" for multi-class lessons.',
    )
    color: str = Field(default="", description="Hex colour used in the SchoolSoft UI.")
    attendance_status: str = Field(
        default="",
        description='Per-student status if reported, e.g. "Närvarande", "Frånvarande".',
    )


class AllDayEvent(BaseModel):
    """Multi-day or all-day band shown above the weekday columns.

    Examples seen in the calendar UI: 'Idrott åk5' (a sport day) or
    'Planering svenska (fylls på kontinuerligt)' (a rolling planning slot).
    """

    title: str
    start_day: str = ""
    end_day: str = ""
    description: str = ""


class ScheduleWeek(BaseModel):
    week: int
    year: int
    school: str
    student_id: int | None = Field(
        default=None,
        description="Which child's schedule this is, when the parent account has multiple.",
    )
    lessons: list[Lesson]
    all_day_events: list[AllDayEvent] = Field(default_factory=list)
    note: str | None = Field(
        default=None,
        description="Optional message about parser status, e.g. experimental warnings.",
    )
    as_of: AsOf | None = None


class HomeworkItem(BaseModel):
    """One assignment / läxa entry as shown on the start-page list.

    The REST API returns a flat ``subTitle`` like
    ``"ons 13 maj 00:00 - ons 20 maj 00:00, Diagnos, Moderna språk"``.
    We expose both the raw subtitle and the parsed-out parts so callers
    can choose whichever is convenient.
    """

    title: str = ""
    subject: str = Field(default="", description="Subject name extracted from subtitle.")
    kind: str = Field(
        default="",
        description='Type label from subtitle, e.g. "Diagnos", "Inlämningsuppgift".',
    )
    date_range: str = Field(
        default="",
        description="Human-readable date range from subtitle.",
    )
    subtitle: str = Field(default="", description="Raw subtitle text from the API.")
    due: str | None = Field(
        default=None,
        description='Due date as ISO YYYY-MM-DD (parsed from REST sortDate).',
    )
    read: bool = False
    submission_status: str = Field(
        default="",
        description='e.g. "NO_STATUS", "SUBMITTED".',
    )
    result_status: str = Field(
        default="",
        description='e.g. "NOT_REPORTED", "REPORTED".',
    )
    assignment_id: int | None = None
    activity_id: int | None = None
    # Legacy field kept for backward-compatibility with the old JSP parser.
    description: str = ""
    assigned: str | None = None


class HomeworkList(BaseModel):
    school: str
    items: list[HomeworkItem]
    week: int | None = None
    year: int | None = None
    note: str | None = None
    as_of: AsOf | None = None


class PlanningPart(BaseModel):
    """One lesson-planning entry (planeringsdel).

    Closely mirrors :class:`HomeworkItem` but without
    submission/result statuses (planeringar aren't submitted by the
    student — they describe what the *teacher* is planning).
    """

    title: str = ""
    subject: str = Field(default="", description="Subject name extracted from subtitle.")
    kind: str = Field(
        default="",
        description='Type label, typically "Planering".',
    )
    date_range: str = Field(
        default="",
        description="Human-readable date range from subtitle.",
    )
    subtitle: str = Field(default="", description="Raw subtitle text from the API.")
    read: bool = False
    part_id: int | None = Field(default=None, description="ID of this individual part.")
    planning_id: int | None = Field(
        default=None,
        description="ID of the parent planning block this part belongs to.",
    )
    activity_id: int | None = None


class PlanningList(BaseModel):
    school: str
    items: list[PlanningPart]
    week: int | None = None
    year: int | None = None
    note: str | None = None
    as_of: AsOf | None = None


class AttendanceWeek(BaseModel):
    """Per-week attendance summary as shown on Frånvaro → Rapport.

    All counts are number-of-lessons. ``-`` cells in the source render as 0.
    Percentages are floats 0.0-100.0 when SchoolSoft prints them, otherwise
    ``None``.
    """

    week: int = Field(description="ISO week number (1-53).")
    total_present_count: int = 0
    total_present_percent: float | None = None
    unreported_absence_count: int = 0
    unreported_absence_percent: float | None = None
    reported_absence_count: int = 0
    reported_absence_percent: float | None = None
    # Detailed sub-counts (some installations show fewer columns).
    present: int = 0
    present_other_assignment: int = Field(default=0, description="Närvaro: Annat skoluppdrag.")
    left_lesson: int = Field(default=0, description="Närvaro: Avvek från lektion.")
    present_preregistered: int = Field(default=0, description="Närvaro: Föranmäld frånvaro.")
    late_arrival: int = Field(default=0, description="Närvaro: Sen ankomst.")
    absent: int = Field(default=0, description="Frånvarande (uncategorised).")
    preregistered: int = Field(default=0, description="Föranmäld.")
    leave_granted: int = Field(default=0, description="Ledighetsansökan beviljad.")


class AttendanceReport(BaseModel):
    school: str
    weeks: list[AttendanceWeek]
    note: str | None = None
    as_of: AsOf | None = None


class UnreportedAbsenceEvent(BaseModel):
    """A single unreported-absence row from Frånvaro → Oanmäld frånvaro."""

    week: int = Field(description="ISO week number.")
    day: str = Field(default="", description='Swedish weekday name, e.g. "Onsdag".')
    lesson: str = Field(
        default="",
        description='Time + subject as shown on the page, e.g. "8:30-9:20 NO".',
    )
    message: str = Field(
        default="",
        description='School-side status note, e.g. "SMS skickades", "Korrigerad anmälan".',
    )


class UnreportedAbsenceList(BaseModel):
    school: str
    events: list[UnreportedAbsenceEvent]
    note: str | None = None
    as_of: AsOf | None = None


class GradeEntry(BaseModel):
    """One subject grade for one term."""

    subject: str = Field(description="Subject name, e.g. 'Matematik'.")
    term: str = Field(
        default="",
        description='Term label as shown in the column header, e.g. "25/26 Ht".',
    )
    grade: str = Field(
        default="",
        description="Grade letter (A-F) or whatever scale the school uses. "
        "Empty when the cell is blank.",
    )
    note: str = Field(default="", description='Free-text "Notering" from the row.')


class GradeList(BaseModel):
    school: str
    grades: list[GradeEntry]
    terms: list[str] = Field(
        default_factory=list,
        description="All term labels seen across the table, in column order.",
    )
    note: str | None = None
    as_of: AsOf | None = None


class SchoolInformation(BaseModel):
    """The Skolinformation page rendered as plain text.

    SchoolSoft serves this as free-form CMS-edited HTML, so we don't try
    to impose structure beyond extracting visible text. The model can
    pick out hours, contacts, term dates etc. from the text.
    """

    school: str
    text: str = Field(description="Plain-text content of the page.")
    note: str | None = None
    as_of: AsOf | None = None


class Contact(BaseModel):
    """A single contact entry (typically a classmate or their guardian)."""

    name: str = ""
    phone: str = Field(
        default="",
        description='Phone number, may include format hints like "(b)" for bostad.',
    )
    address: str = Field(default="", description="Postal address.")


class ContactList(BaseModel):
    """All contacts from one of SchoolSoft's contact pages."""

    school: str
    contacts: list[Contact]
    note: str | None = None
    as_of: AsOf | None = None


class LibraryFile(BaseModel):
    """One file in the school's shared library / filer & länkar."""

    title: str = Field(description="Display name as shown on the page.")
    filename: str = Field(default="", description="Clean filename from the link's title attribute.")
    description: str = Field(default="", description="Optional description text.")
    size_bytes: int | None = None
    content_type: str | None = Field(
        default=None,
        description="Best-effort guess from filename; the real type is "
        "only known after fetching the file.",
    )
    request_id: int | None = Field(
        default=None,
        description="ID for the right_student_library_download.jsp?requestid= endpoint.",
    )
    category: str = Field(
        default="",
        description="Section heading the file appeared under, e.g. 'Policies', 'Blanketter'.",
    )


class LibraryFileList(BaseModel):
    school: str
    files: list[LibraryFile]
    note: str | None = None
    as_of: AsOf | None = None


class Attachment(BaseModel):
    """A file attached to a news item or message.

    To download, call download_attachment(news_id=..., type_id=..., fileid=...)
    or read_attachment_text(...) for an LLM-friendly text extraction.
    """

    fileid: int = Field(description="SchoolSoft's internal file identifier")
    filename: str = ""
    size_bytes: int | None = None
    content_type: str | None = Field(
        default=None,
        description="Best-effort guess from the filename; the real type is "
        "only known after fetching the file.",
    )


class NewsItem(BaseModel):
    title: str = ""
    date: str = ""
    author: str = ""
    body: str = ""
    news_id: int | None = Field(
        default=None,
        description="Pass to get_news_item() for the full body and attachments.",
    )
    type_id: int = Field(
        default=1,
        description="SchoolSoft news 'type' enum. 1 = current/aktuella, 2 = older/äldre.",
    )
    category: str = Field(
        default="",
        description="Section heading on the news page (e.g. 'ALLMÄN information', 'VECKOBREV').",
    )
    recipient: str = ""
    published: str = ""
    visible_until: str = ""
    attachments: list[Attachment] = Field(default_factory=list)


class NewsFeed(BaseModel):
    school: str
    items: list[NewsItem]
    note: str | None = None
    as_of: AsOf | None = None


class Message(BaseModel):
    subject: str = ""
    sender: str = ""
    date: str = ""
    body: str = ""
    unread: bool = False
    message_id: int | None = None
    attachments: list[Attachment] = Field(default_factory=list)


class MessageList(BaseModel):
    school: str
    items: list[Message]
    note: str | None = None
    as_of: AsOf | None = None


class AttachmentBytes(BaseModel):
    """Binary attachment payload, base64-encoded for MCP transport."""

    filename: str
    content_type: str
    size_bytes: int
    data_base64: str = Field(
        default="",
        description="Raw file bytes, base64-encoded. Decode before writing to disk. "
        "Empty when the file exceeded ``max_bytes`` — see ``note``.",
    )
    note: str | None = Field(
        default=None,
        description="Populated when the download was skipped (e.g. file too large). "
        "Try ``read_attachment_text`` for content, or pass a larger ``max_bytes``.",
    )


class AttachmentText(BaseModel):
    """Plain-text extraction of an attachment, for LLM consumption."""

    filename: str
    content_type: str
    size_bytes: int
    text: str
    truncated: bool = False
    next_offset: int | None = Field(
        default=None,
        description="Set when ``truncated=True``. Pass as ``offset`` in the "
        "next ``read_attachment_text`` call to continue reading.",
    )
    note: str | None = None
