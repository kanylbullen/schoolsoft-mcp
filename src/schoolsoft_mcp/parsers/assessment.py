"""Sammantagen bedömning, results, and the "currently open work" list.

Three parent views that the rest of this server did not reach.

**Sammantagen bedömning** (``react/#/parent/holistic_assessment``) is what a
Swedish school publishes for the years that carry no formal grades. For a
year-6 pupil the Betyg page :mod:`.grades` reads is close to empty while
this one holds everything the teachers have said. It is also where
``subjectWarning`` lives — the school's flag that a subject risks not
reaching the goals, which is the single most important field on the parent
surface and the one a family most wants pushed at them rather than found.

**Resultat** and **Uppgifter → Aktuella** are the two tabs of the subject
room that :mod:`.subjectrooms` left alone: published results, and the work
that is open right now regardless of which week it falls in.

Endpoints, all under ``rest-api/<usertype>/`` and all confirmed against a
live parent account:

``holistic_assessment/rows``
    One row per subject: the assessment text, ``subjectWarning``, update and
    publish timestamps, and whether the guardian has read it.
``holistic_assessment/options``
    ``{id, label}`` per subject, where ``label`` carries the subject code.
``holistic_assessment/<id>``
    Header: pupil, subject, group, publish status.
``holistic_assessment/<id>/sections/published``
    Which sections the school actually publishes to guardians.
``holistic_assessment/<id>/knowledge_development/view``
    ``{value, supportMeasures, updatedByInfo}`` — the assessment itself, any
    support measures, and who last touched it.
``holistic_assessment/<id>/formative_comments``
    The teacher's written comments.
``holistic_assessment/<id>/subject_warning``
    ``{active, comment, ...}`` — the risk flag and its motivation.
``holistic_assessment/<id>/assessed_assignments``
    The graded work behind the assessment, with the grade in ``review``.
``holistic_assessment/<id>/reporting_occasion_assessment``
    The same subject at earlier reporting occasions, so history is reachable.
``ps/subjectroom/results/grid/rows``
    Published results. Carries no grade value — the grade is on the
    assessment's ``assessed_assignments``, which is why the two live here
    together.
``ps/subjectroom/table/rows``
    Everything open right now, week-independent.
``ps/assignment/<id>/assessment``
    The result itself, as the guardian sees it after clicking a row in the
    results list (the SPA routes to ``subjectrooms/<activity>/assignment/<id>``
    and fetches this next to ``ps/assignments/<id>/view``). ``review`` is
    the grade or wording ("B", "Når målen väl", "Ej närvarande"),
    ``teacherComment`` the feedback, ``assessedCriteriaTabs`` the criteria
    levels reached. Works on grading years too, where the subject
    assessment has no assessed work behind it.
"""

from __future__ import annotations

import logging
from typing import Any

from ._fields import int_field, str_field
from .subjectrooms import usertype_segment

logger = logging.getLogger(__name__)

_s = str_field
_i = int_field

# ---------------------------------------------------------------------------
# Paths. ``{ut}`` is the usertype segment ("parent" or "student").
# ---------------------------------------------------------------------------
ASSESSMENT_ROWS = "rest-api/{ut}/holistic_assessment/rows"
ASSESSMENT_OPTIONS = "rest-api/{ut}/holistic_assessment/options"
ASSESSMENT_OVERVIEW = "rest-api/{ut}/holistic_assessment/overview"
ASSESSMENT_HEADER = "rest-api/{ut}/holistic_assessment/{assessment_id}"
ASSESSMENT_SECTIONS = (
    "rest-api/{ut}/holistic_assessment/{assessment_id}/sections/published"
)
ASSESSMENT_KNOWLEDGE = (
    "rest-api/{ut}/holistic_assessment/{assessment_id}/knowledge_development/view"
)
ASSESSMENT_COMMENTS = (
    "rest-api/{ut}/holistic_assessment/{assessment_id}/formative_comments"
)
ASSESSMENT_WARNING = (
    "rest-api/{ut}/holistic_assessment/{assessment_id}/subject_warning"
)
ASSESSMENT_WORK = (
    "rest-api/{ut}/holistic_assessment/{assessment_id}/assessed_assignments"
)
ASSESSMENT_HISTORY = (
    "rest-api/{ut}/holistic_assessment/{assessment_id}/reporting_occasion_assessment"
)

RESULT_ROWS = "rest-api/{ut}/ps/subjectroom/results/grid/rows"
OPEN_WORK_ROWS = "rest-api/{ut}/ps/subjectroom/table/rows"
ASSIGNMENT_ASSESSMENT = "rest-api/{ut}/ps/assignment/{assignment_id}/assessment"


def path_for(template: str, usertype: int, **kwargs: Any) -> str:
    """Fill ``{ut}`` from a numeric usertype plus any other slots."""
    return template.format(ut=usertype_segment(usertype), **kwargs)


def subject_list(entry: dict[str, Any], key: str = "subjects") -> str:
    """``[{name, color}]`` or ``["Bild"]`` or ``"Bild"`` -> ``"Bild, Slöjd"``."""
    value = entry.get(key)
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    names: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            name = _s(item, "name")
            if name:
                names.append(name)
    return ", ".join(dict.fromkeys(names))


def subject_code(label: str) -> str:
    """``"Idrott och hälsa (IDH)"`` -> ``"IDH"``. Empty when unparenthesised."""
    if label.endswith(")") and "(" in label:
        return label[label.rindex("(") + 1 : -1].strip()
    return ""


def parse_assessment_rows(
    rows: Any, options: Any = None
) -> list[dict[str, Any]]:
    """One dict per subject from ``holistic_assessment/rows``.

    ``options`` is merged in when given: it is the only place the subject's
    short code appears, and the code is what a schedule row uses.
    """
    codes: dict[int, str] = {}
    if isinstance(options, list):
        for option in options:
            if not isinstance(option, dict):
                continue
            oid = _i(option, "id")
            code = subject_code(_s(option, "label"))
            if oid is not None and code:
                codes[oid] = code

    out: list[dict[str, Any]] = []
    for entry in rows if isinstance(rows, list) else []:
        if not isinstance(entry, dict):
            continue
        assessment_id = _i(entry, "holisticAssessmentId")
        out.append(
            {
                "assessment_id": assessment_id,
                "subject": _s(entry, "title"),
                "subject_code": codes.get(assessment_id or -1, ""),
                "assessment": _s(entry, "subTitle"),
                "subject_warning": bool(entry.get("subjectWarning")),
                "published": bool(entry.get("published")),
                "read": bool(entry.get("read")),
                "updated_at": _s(entry, "updatedAt") or None,
                "updated_label": _s(entry, "friendlyUpdatedAt"),
                "published_at": _s(entry, "publishedAt") or None,
                "published_label": _s(entry, "friendlyPublishedAt"),
            }
        )
    out.sort(key=lambda r: (not r["subject_warning"], r["subject"]))
    return out


def parse_knowledge_development(payload: Any) -> dict[str, str]:
    """``{value, supportMeasures, updatedByInfo}`` -> flat strings."""
    if not isinstance(payload, dict):
        return {"assessment": "", "support_measures": "", "updated_by": ""}
    return {
        "assessment": _s(payload, "value"),
        "support_measures": _s(payload, "supportMeasures"),
        "updated_by": _s(payload, "updatedByInfo"),
    }


def parse_subject_warning(payload: Any) -> dict[str, Any]:
    """The "risk att inte nå målen" flag.

    ``active`` is the flag; ``published`` is whether the guardian is meant to
    see it. An active-but-unpublished warning is the school still writing,
    and reporting it as though it had been communicated would be wrong.
    """
    if not isinstance(payload, dict):
        return {"active": False, "published": False, "comment": "", "updated": ""}
    updated = _s(payload, "lastUpdatedAt")
    return {
        "active": bool(payload.get("active")),
        "published": bool(payload.get("published")),
        "comment": _s(payload, "comment"),
        "updated": "" if updated in {"-", ""} else updated,
    }


def parse_assessed_work(rows: Any) -> list[dict[str, Any]]:
    """The graded work behind an assessment. ``review`` is the grade."""
    out: list[dict[str, Any]] = []
    for entry in rows if isinstance(rows, list) else []:
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "assignment_id": _i(entry, "id"),
                "activity_id": _i(entry, "activityId"),
                "title": _s(entry, "name"),
                "kind": _s(entry, "type"),
                "grade": _s(entry, "review"),
                "points": _s(entry, "points"),
                "comment": _s(entry, "formativeComment"),
            }
        )
    return out


def parse_comments(rows: Any) -> list[str]:
    """Formative comments, flattened to the text a guardian reads."""
    out: list[str] = []
    for entry in rows if isinstance(rows, list) else []:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
        elif isinstance(entry, dict):
            text = _s(entry, "comment") or _s(entry, "text") or _s(entry, "value")
            if text:
                out.append(text)
    return out


def parse_result_rows(rows: Any) -> list[dict[str, Any]]:
    """Published results from ``ps/subjectroom/results/grid/rows``.

    Deliberately carries no grade field: this endpoint has none. The grade
    sits on the subject's assessment, and inventing an empty ``grade`` key
    here would read as "no grade given" rather than "not on this endpoint".
    """
    out: list[dict[str, Any]] = []
    for entry in rows if isinstance(rows, list) else []:
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "assignment_id": _i(entry, "assignmentId"),
                "activity_id": _i(entry, "activityId"),
                "title": _s(entry, "title"),
                "subject": subject_list(entry),
                "kind": _s(entry, "assignmentType"),
                "teacher": _s(entry, "teacher"),
                "published": _s(entry, "publishDate") or None,
                "read": bool(entry.get("read")),
            }
        )
    out.sort(key=lambda r: r["published"] or "", reverse=True)
    return out


def parse_result_assessment(payload: Any) -> dict[str, Any]:
    """One assignment's published assessment from ``ps/assignment/<id>/assessment``.

    ``review`` is the result. Criteria come as tabs per subject, each with
    the level reached and the criterion text for every level; only the text
    of the level reached is kept, since that is what the guardian is shown
    highlighted. ``partial_moment_count`` is a count rather than a parse:
    every live payload seen so far had an empty list, so its row shape is
    unverified and inventing fields for it would read as data.
    """
    if not isinstance(payload, dict):
        payload = {}
    criteria: list[dict[str, Any]] = []
    for tab in payload.get("assessedCriteriaTabs") or []:
        if not isinstance(tab, dict):
            continue
        content = tab.get("content")
        subject = _s(content, "name") if isinstance(content, dict) else ""
        for item in tab.get("assessedCriteria") or []:
            if not isinstance(item, dict):
                continue
            level = item.get("level")
            level = level if isinstance(level, dict) else {}
            reached = _s(level, "levelEnum")
            text = ""
            for step in item.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                step_level = step.get("level")
                if isinstance(step_level, dict) and _s(step_level, "levelEnum") == reached:
                    text = _s(step, "text")
                    break
            criteria.append(
                {
                    "subject": subject,
                    "level": _s(level, "description"),
                    "level_enum": reached,
                    "criterion": text,
                }
            )
    moments = payload.get("assessmentPartialMoments")
    return {
        "review": _s(payload, "review"),
        "teacher_comment": _s(payload, "teacherComment"),
        "student_comment": _s(payload, "studentComment"),
        "criteria": criteria,
        "partial_moment_count": len(moments) if isinstance(moments, list) else 0,
    }


def parse_open_work(rows: Any) -> list[dict[str, Any]]:
    """Everything open right now, from ``ps/subjectroom/table/rows``.

    The week-scoped assignment query answers "what falls in week N". This
    answers "what is outstanding", which is a different question and the one
    the UI's *Aktuella* tab asks.
    """
    out: list[dict[str, Any]] = []
    for entry in rows if isinstance(rows, list) else []:
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "entity_id": _i(entry, "id"),
                "part_id": _i(entry, "partId"),
                "entity_type": _s(entry, "entityType"),
                "activity_id": _i(entry, "activityId"),
                "title": _s(entry, "title"),
                "subject": subject_list(entry, "subjectNames"),
                "kind": _s(entry, "type"),
                "end_date": _s(entry, "endDate") or None,
                "end_time": _s(entry, "endTime"),
                "status": _s(entry, "status"),
                "submission_status": _s(entry, "submissionStatus"),
                "result_status": _s(entry, "resultReportStatus"),
                "read": bool(entry.get("read")),
            }
        )
    out.sort(key=lambda r: (r["end_date"] or "9999", r["end_time"] or ""))
    return out


def parse_history(rows: Any) -> list[dict[str, Any]]:
    """Earlier reporting occasions for the same subject."""
    out: list[dict[str, Any]] = []
    for entry in rows if isinstance(rows, list) else []:
        if not isinstance(entry, dict):
            continue
        occasion = entry.get("reportingOccasion")
        occasion = occasion if isinstance(occasion, dict) else {}
        out.append(
            {
                "assessment_id": _i(entry, "assessmentId"),
                "current": bool(entry.get("current")),
                "term": _s(occasion, "name"),
                "occasion_date": _s(occasion, "occasion") or None,
            }
        )
    return out
