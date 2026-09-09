"""Shared pydantic models returned by the MCP tools."""

from __future__ import annotations

from pydantic import BaseModel, Field

# ISO order, so ``DAY_KEYS[date.isoweekday() - 1]`` is that date's key.
DAY_KEYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
# The school week. Derived rather than written out again: the schedule join
# compares a ``Lesson.day`` built from this against an index into DAY_KEYS,
# and two hand-maintained tables would only be coincidentally aligned.
WEEKDAYS: tuple[str, ...] = DAY_KEYS[:5]

DAY_NAMES_SV: dict[str, str] = {
    "monday": "måndag",
    "tuesday": "tisdag",
    "wednesday": "onsdag",
    "thursday": "torsdag",
    "friday": "fredag",
    "saturday": "lördag",
    "sunday": "söndag",
}


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
    grade: str = Field(
        default="",
        description='Year as the school labels it, e.g. "6" for "<school> 6".',
    )
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

    teacher: str = Field(default="", description="Teacher who published it.")
    start_date: str | None = Field(
        default=None, description="ISO YYYY-MM-DD start, from the grid endpoint."
    )
    end_date: str | None = Field(
        default=None,
        description="ISO YYYY-MM-DD end. More reliable than ``due``, which is "
        "parsed out of a prose subtitle.",
    )
    publish_date: str | None = None
    status: str = Field(default="", description="ONGOING / EXPIRED.")
    body: str = Field(
        default="",
        description="The assignment's full description, HTML flattened to plain "
        "text. Empty unless the tool was called with include_body=True.",
    )
    material: list[MaterialLink] = Field(
        default_factory=list,
        description="Files and links attached to the assignment. Only "
        "``get_planning_detail`` fills this in; on a listing it is always "
        "empty, which means 'not fetched', not 'no attachments'.",
    )


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
    activity_id: int | None = Field(
        default=None,
        description="Subject-room ID. Join key for get_subject_rooms() and for "
        "everything else hanging off the same subject.",
    )
    teacher: str = Field(default="", description="Teacher who published the planning.")
    start_date: str | None = Field(
        default=None, description="ISO YYYY-MM-DD start, when known."
    )
    end_date: str | None = Field(
        default=None, description="ISO YYYY-MM-DD end, when known."
    )
    publish_date: str | None = None
    status: str = Field(
        default="", description='Lifecycle from SchoolSoft: ONGOING / EXPIRED.'
    )
    body: str = Field(
        default="",
        description="The teacher's actual planning text, HTML flattened to plain "
        "text. Empty when the tool was called with include_body=False. This is "
        "where per-week detail lives — which activity, which meeting point, what "
        "to bring.",
    )
    week_lines: list[str] = Field(
        default_factory=list,
        description="Lines of ``body`` that explicitly name the requested ISO "
        'week, e.g. "v.37 Orientering (samling vid klubbstugan)". Term-long '
        "plannings are in force every day but only one line applies to any "
        "given week — this is that line. Empty when the body is not organised "
        "by week, in which case read ``body``.",
    )
    mentions_weeks: bool = Field(
        default=False,
        description="True when the body is organised by week number at all. "
        "With ``week_lines`` empty this means the teacher wrote about other "
        "weeks but not the one you asked for — do not read ``body`` as if it "
        "described the requested week.",
    )
    material: list[MaterialLink] = Field(
        default_factory=list,
        description="Files and links attached to the planning. Only "
        "``get_planning_detail`` fills this in; on a listing it is always "
        "empty, which means 'not fetched', not 'no attachments'.",
    )


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
    acknowledged_by: str = Field(
        default="",
        description="Guardian who ticked \"tagit del av\" for this lesson. Empty "
        "means nobody has yet.",
    )
    acknowledged_at: str = Field(
        default="",
        description='When it was acknowledged, as the page prints it, e.g. '
        '"2026-09-09 7:11".',
    )
    school_confirmed: str = Field(
        default="",
        description='Contents of the "Bekräftad av skolan" column. Usually empty.',
    )


class UnreportedAbsenceList(BaseModel):
    """Unreported absence, split by whether a guardian has acknowledged it.

    SchoolSoft texts a guardian when a lesson is missed, and the guardian is
    then expected to open the page and confirm they have seen it. The page
    shows the two groups in separate tables with the same columns, so a
    parser that takes the first table it recognises reports weeks-old,
    already-handled absences as outstanding — every day, forever.
    """

    school: str
    events: list[UnreportedAbsenceEvent] = Field(
        default_factory=list,
        description="Absences NOT yet acknowledged — the ones to act on. A "
        "guardian has to open SchoolSoft and confirm each; this server "
        "deliberately does not do it for them.",
    )
    acknowledged: list[UnreportedAbsenceEvent] = Field(
        default_factory=list,
        description="Absences a guardian has already confirmed, with who and "
        "when. History, not a to-do list. Do not report these as new.",
    )
    confirmed_none_pending: bool = Field(
        default=False,
        description="True when the page itself stated there is nothing to "
        "acknowledge. An empty ``events`` without this flag means the parser "
        "found no rows, which is not the same as the school saying so.",
    )
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


class MaterialLink(BaseModel):
    """A file or link a teacher attached to a planning or assignment."""

    kind: str = Field(description='"file" or "link".')
    name: str = ""
    url: str | None = None
    file_id: int | None = Field(
        default=None,
        description="SchoolSoft's internal id for the file. There is no tool "
        "that takes it — fetch the file through ``url`` instead. Present so a "
        "caller can tell two attachments with the same name apart.",
    )


class SubjectRoom(BaseModel):
    """One ämnesrum — a subject the child is enrolled in.

    ``activity_id`` is the join key across the whole modern planning
    surface: plannings, assignments, teachers and lessons all carry it.
    """

    activity_id: int
    subject: str = ""
    groups: list[str] = Field(default_factory=list)
    color: str = ""
    has_access: bool = Field(
        default=False,
        description="Whether the room's own page is open to this account.",
    )
    teachers: list[str] = Field(default_factory=list)


class SubjectRoomList(BaseModel):
    school: str
    rooms: list[SubjectRoom]
    note: str | None = None
    as_of: AsOf | None = None


class PlanningDetail(BaseModel):
    """One planning with its full body — the teacher's actual text."""

    part_id: int | None = None
    planning_id: int | None = None
    activity_id: int | None = None
    title: str = ""
    subject: str = ""
    teacher: str = ""
    date_range: str = Field(
        default="", description="Prose range from SchoolSoft, e.g. 'onsdag 19 augusti "
        "2026 - torsdag 31 december 2026'."
    )
    start_date: str | None = None
    end_date: str | None = None
    publish_date: str | None = None
    status: str = ""
    read: bool = False
    body: str = Field(
        default="", description="Full planning text, HTML flattened to plain text."
    )
    week_lines: list[str] = Field(
        default_factory=list,
        description="Lines of ``body`` naming the requested week, when one was given.",
    )
    mentions_weeks: bool = Field(
        default=False,
        description="True when the body is organised by week number at all.",
    )
    material: list[MaterialLink] = Field(default_factory=list)
    note: str | None = None
    as_of: AsOf | None = None


class ExamEntry(BaseModel):
    """An announced exam (Prov) from the exam schedule."""

    exam_id: int | None = None
    title: str = ""
    kind: str = Field(default="", description='Type label, typically "Prov".')
    start: str | None = Field(
        default=None,
        description="ISO datetime the announcement becomes visible — NOT when "
        "the exam is written. The exam's own date is on the matching "
        "assignment in ``get_homework``.",
    )
    end: str | None = Field(
        default=None, description="ISO datetime the announcement stops showing."
    )


class ExamSchedule(BaseModel):
    school: str
    exams: list[ExamEntry]
    note: str | None = None
    as_of: AsOf | None = None


class LessonDetail(BaseModel):
    """Room, teachers and group for a single scheduled lesson."""

    lesson_id: int
    title: str = ""
    room: str = ""
    teachers: list[str] = Field(default_factory=list)
    groups: str = ""
    note: str | None = None
    as_of: AsOf | None = None


class DayLesson(BaseModel):
    """A lesson on the briefing's day, with what is planned for it."""

    start: str = ""
    end: str = ""
    subject: str = ""
    teacher: str = ""
    room: str = ""
    activity_id: int | None = None
    attendance_status: str = ""
    is_break: bool = False
    planning_titles: list[str] = Field(
        default_factory=list,
        description="Titles of plannings covering this lesson's subject. A "
        "non-empty list with an empty ``plannings`` means a planning exists "
        "but says nothing about this particular week.",
    )
    plannings: list[str] = Field(
        default_factory=list,
        description="What the planning says for this week — the week-numbered "
        "line when the teacher wrote one, otherwise the opening of the body. "
        "The sentence a parent needs before the child leaves the house.",
    )


class DayBriefing(BaseModel):
    """Everything that matters about one school day, joined server-side.

    Assembled from the schedule, plannings (with bodies), assignments,
    exams, unreported absence and recent news in one call. The point is
    that the joining is *not* left to the caller: the failure mode this
    replaces is a model fetching the schedule, seeing "Idrott", and never
    fetching the planning that says where Idrott is that week.
    """

    school: str
    date: str = Field(description="ISO date the briefing is for.")
    weekday: str = Field(description="Swedish weekday name.")
    iso_week: int
    iso_year: int
    student_id: int | None = None
    student_name: str = ""
    is_school_day: bool = Field(
        default=True, description="False when the schedule has no lessons that day."
    )
    lessons: list[DayLesson] = Field(default_factory=list)
    prepare: list[str] = Field(
        default_factory=list,
        description="Preparation-critical items derived from the day: meeting "
        "points, kit, outings, work due today or tomorrow, anything the "
        "plannings flag. Each entry is a ready-to-read sentence. Announced "
        "exams are NOT here — see ``exams``, whose dates are the "
        "announcement window rather than the exam date.",
    )
    due_today: list[HomeworkItem] = Field(default_factory=list)
    due_soon: list[HomeworkItem] = Field(
        default_factory=list, description="Due within the next 7 days."
    )
    plannings: list[PlanningDetail] = Field(
        default_factory=list,
        description="The plannings behind this day's lessons, with bodies and "
        "week lines. Subjects not on the timetable today are left out — their "
        "bodies are not fetched, because a day briefing is about the day. Use "
        "``get_planning`` for the full term listing.",
    )
    exams: list[ExamEntry] = Field(default_factory=list)
    unreported_absence: list[UnreportedAbsenceEvent] = Field(default_factory=list)
    news: list[NewsItem] = Field(
        default_factory=list,
        description="News/veckobrev from the last ``news_days`` days. Items "
        "with an unreadable date are kept rather than dropped.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Sections that could not be fetched. The briefing is still "
        "returned — a partial day is more useful than an exception.",
    )
    note: str | None = None
    as_of: AsOf | None = None


class SubjectAssessment(BaseModel):
    """One subject's row in Sammantagen bedömning."""

    assessment_id: int | None = Field(
        default=None,
        description="Pass to ``get_assessment_detail`` for the teacher's text, "
        "the graded work behind it, and any warning motivation.",
    )
    subject: str = ""
    subject_code: str = Field(
        default="",
        description='Short code as the schedule spells it, e.g. "IDH". Empty '
        "when the subject list did not supply one.",
    )
    assessment: str = Field(
        default="",
        description='The school\'s wording, e.g. "Godtagbara kunskaper", "Mer '
        'än godtagbara kunskaper", "Ingen bedömning".',
    )
    subject_warning: bool = Field(
        default=False,
        description="The school has flagged that this subject risks not "
        "reaching the goals. This is the field a guardian most needs pushed "
        "at them; ``get_assessment_detail`` carries the motivation.",
    )
    published: bool = True
    read: bool = Field(
        default=False, description="Whether a guardian has opened it."
    )
    updated_at: str | None = None
    updated_label: str = Field(
        default="", description='As the page prints it, e.g. "28 maj 13:28".'
    )
    published_at: str | None = None
    published_label: str = ""


class AssessmentList(BaseModel):
    """Sammantagen bedömning for one child, warnings first.

    This is what a Swedish school publishes for the years that carry no
    formal grades. On those years ``get_grades`` is close to empty and this
    holds everything the teachers have said.
    """

    school: str
    student_id: int | None = None
    subjects: list[SubjectAssessment] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description="Subjects flagged as at risk. Empty is the normal case "
        "and is worth stating plainly rather than omitting.",
    )
    unread: int = 0
    note: str | None = None
    as_of: AsOf | None = None


class AssessedWork(BaseModel):
    """A graded piece of work behind a subject assessment."""

    assignment_id: int | None = None
    activity_id: int | None = None
    title: str = ""
    kind: str = Field(default="", description='e.g. "Prov", "Arbete under lektionstid".')
    grade: str = Field(
        default="",
        description='The grade the teacher set, e.g. "B". Empty means not '
        "graded, or graded on criteria rather than a letter.",
    )
    points: str = ""
    comment: str = Field(default="", description="Teacher's comment on this work.")


class AssessmentTerm(BaseModel):
    """The same subject at an earlier reporting occasion."""

    assessment_id: int | None = None
    current: bool = False
    term: str = Field(default="", description='e.g. "HT 25".')
    occasion_date: str | None = None


class AssessmentDetail(BaseModel):
    """One subject's assessment in full."""

    school: str
    assessment_id: int
    student_name: str = ""
    subject: str = ""
    group: str = ""
    publish_status: str = ""
    assessment: str = Field(
        default="", description="The school's wording for the knowledge level."
    )
    support_measures: str = Field(
        default="",
        description="Support the school has put in place. Empty is common.",
    )
    updated_by: str = Field(
        default="", description='e.g. "Senast uppdaterad 28 maj 13:28 av <teacher>".'
    )
    subject_warning: bool = False
    warning_published: bool = Field(
        default=False,
        description="An active warning that is not published is the school "
        "still writing. Do not report it as something the family was told.",
    )
    warning_comment: str = ""
    comments: list[str] = Field(
        default_factory=list, description="Formative comments from the teacher."
    )
    assessed_work: list[AssessedWork] = Field(default_factory=list)
    published_sections: list[str] = Field(
        default_factory=list,
        description="Which sections this school publishes to guardians, e.g. "
        "ATTENDANCE, FORMATIVE_COMMENT, KNOWLEDGE_DEVELOPMENT. A section not "
        "listed here is not withheld from you by this tool — the school does "
        "not publish it.",
    )
    terms: list[AssessmentTerm] = Field(default_factory=list)
    note: str | None = None
    as_of: AsOf | None = None


class ResultEntry(BaseModel):
    """A published result from the subject room's Resultat tab."""

    assignment_id: int | None = None
    activity_id: int | None = None
    title: str = ""
    subject: str = ""
    kind: str = ""
    teacher: str = ""
    published: str | None = Field(
        default=None, description="When the result was published."
    )
    read: bool = False


class ResultList(BaseModel):
    """Published results.

    SchoolSoft's results list carries no grade value; it says *that* a result
    was published, not what it was. The result itself is one call further in:
    ``get_result_detail(assignment_id)``, whose ``review`` carries it. This
    model therefore has no grade field rather than an empty one that would
    read as "no grade given".
    """

    school: str
    student_id: int | None = None
    results: list[ResultEntry] = Field(default_factory=list)
    unread: int = 0
    note: str | None = None
    as_of: AsOf | None = None


class ResultCriterion(BaseModel):
    """One criterion level reached on an assessed assignment."""

    subject: str = ""
    level: str = Field(default="", description='e.g. "Når C nivå".')
    level_enum: str = Field(default="", description='e.g. "MEET_C_LEVEL".')
    criterion: str = Field(
        default="", description="The criterion text for the level reached."
    )


class ResultDetail(BaseModel):
    """One published result in full — what a guardian sees on the result page.

    ``review`` is the result: a letter grade ("B"), the school's own wording
    ("Når målen väl", "Uppnått målen") or "Ej närvarande" for a missed test.
    An empty ``review`` with an empty comment means the teacher published
    the assignment as assessed without writing anything the guardian can
    read.
    """

    school: str
    student_id: int | None = None
    assignment_id: int
    review: str = Field(
        default="",
        description='The result itself: "B", "Når målen väl", "Ej närvarande" …',
    )
    teacher_comment: str = ""
    student_comment: str = ""
    criteria: list[ResultCriterion] = Field(default_factory=list)
    partial_moment_count: int = Field(
        default=0,
        description="Number of partial moments (delmoment) on the assessment. "
        "Only counted; the row shape has not been seen populated.",
    )
    note: str | None = None
    as_of: AsOf | None = None


class OpenWorkItem(BaseModel):
    """A piece of work that is open right now, whatever week it falls in."""

    entity_id: int | None = None
    part_id: int | None = None
    entity_type: str = Field(default="", description='e.g. "ASSIGNMENT".')
    activity_id: int | None = None
    title: str = ""
    subject: str = ""
    kind: str = ""
    end_date: str | None = None
    end_time: str = ""
    status: str = Field(default="", description='e.g. "ONGOING".')
    submission_status: str = ""
    result_status: str = ""
    read: bool = False


class OpenWorkList(BaseModel):
    """Everything currently open, in due order.

    ``get_homework`` answers "what falls in week N". This answers "what is
    outstanding", which is a different question — a task due in three weeks
    is invisible to the first and present here.
    """

    school: str
    student_id: int | None = None
    items: list[OpenWorkItem] = Field(default_factory=list)
    note: str | None = None
    as_of: AsOf | None = None


class FritidsDay(BaseModel):
    """One day in the fritids month calendar."""

    date: str = Field(description="ISO date.")
    weekday: str = Field(default="", description="Lowercase English weekday.")
    week: int | None = Field(default=None, description="ISO week number.")
    drop_off: str = Field(default="", description='Lämnas, e.g. "8:00". Empty when unbooked.')
    pick_up: str = Field(default="", description='Hämtas, e.g. "16:30". Empty when unbooked.')
    booked: bool = Field(
        default=False,
        description="True when the day has a drop-off and pick-up time. A "
        "weekday with ``booked: false`` inside a fritids child's term is a "
        "day nobody has arranged care for — worth saying out loud.",
    )
    in_month: bool = Field(
        default=True,
        description="False for the leading/trailing days of adjacent months "
        "that the calendar shows to fill its first and last rows.",
    )


class FritidsWeekDay(BaseModel):
    """One weekday in the detailed week block, with the school day beside it."""

    date: str = ""
    weekday: str = ""
    drop_off: str = ""
    pick_up: str = ""
    school_start: str = Field(default="", description="When lessons start that day.")
    school_end: str = Field(
        default="",
        description="When lessons end. The gap to ``pick_up`` is fritids time.",
    )
    guardian_comment: str = Field(
        default="", description="Comment the guardian left for the staff."
    )
    staff_comment: str = Field(
        default="", description="Comment the staff left for the guardian."
    )
    editable: bool = Field(
        default=False,
        description="True for days still to come — the page offers to change "
        "them. This tool does not; it only reads.",
    )


class FritidsTimes(BaseModel):
    """Fritids (after-school care) times for one child, one month at a time.

    The only page on the parent surface that changes what a family does
    every single day for a younger child: when the child is dropped off and
    when somebody must be at the school to collect them.
    """

    school: str
    student_id: int | None = None
    year: int = 0
    month: int = Field(default=0, description="1-12.")
    month_label: str = Field(default="", description='As the page prints it, e.g. "september 2026".')
    days: list[FritidsDay] = Field(default_factory=list)
    week: int | None = Field(default=None, description="ISO week the detail block shows.")
    week_days: list[FritidsWeekDay] = Field(
        default_factory=list,
        description="The detailed week: booked times next to school hours, "
        "and comments in both directions.",
    )
    recurring_weeks: str = Field(
        default="",
        description='The weeks the booked times repeat over, e.g. "37-51, 17-22".',
    )
    opening_hours: str = Field(
        default="", description="Fritids opening hours, when the school states them."
    )
    has_fritids: bool = Field(
        default=False,
        description="False when no day in the month has a booked time — the "
        "child is not enrolled, or nothing is booked. Do not read an empty "
        "``days[].pick_up`` as 'goes home after school' without checking this.",
    )
    note: str | None = None
    as_of: AsOf | None = None


HomeworkItem.model_rebuild()
PlanningPart.model_rebuild()
