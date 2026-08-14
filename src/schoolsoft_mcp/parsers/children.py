"""Parse the parent-header JSON into a :class:`ChildList`.

Observed shape of ``/rest-api/parent/header/parent``::

    {
      "firstName": "...",
      "lastName": "...",
      "children": [
        {
          "id": <int>,
          "firstName": "...",
          "lastName": "...",
          "schools": [{"orgId": <int>, "className": "...", "schoolName": "...",
                       "studentActive": true, "parentAllowedAccess": true}]
        },
        ...
      ],
      "currentChildId": <int>,
      "currentOrgId": <int>
    }

The parser falls back to a wider set of key names so it keeps working
when SchoolSoft renames things, but the keys above are the observed
truth as of 2026-05.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models import Child, ChildList

logger = logging.getLogger(__name__)

_STUDENT_LIST_KEYS = ("children", "students", "studentList", "items")
_ID_KEYS = ("id", "studentId", "student_id", "userId", "childId")
_NAME_KEYS = ("name", "fullName", "displayName")
_FIRST_NAME_KEYS = ("firstName", "first_name", "givenName")
_LAST_NAME_KEYS = ("lastName", "last_name", "familyName", "surname")
_SCHOOL_KEYS = ("schoolName", "school", "schoolLabel")
_GRADE_KEYS = ("className", "class", "grade", "schoolClass")
_ACTIVE_ID_KEYS = (
    "currentChildId",
    "activeChildId",
    "activeStudent",
    "activeStudentId",
    "selectedStudent",
    "currentStudent",
)
_ORG_ID_KEYS = ("orgId", "org_id", "organisationId", "organizationId", "schoolId")
_ACTIVE_ORG_ID_KEYS = ("currentOrgId", "activeOrgId", "selectedOrgId")


def parse_parent_header(payload: Any, *, school: str) -> ChildList:
    if not isinstance(payload, dict):
        return ChildList(
            school=school,
            children=[],
            note="Parent header response was not a JSON object; structure unknown.",
        )

    students_raw = _first_list(payload, _STUDENT_LIST_KEYS)
    if students_raw is None:
        return ChildList(
            school=school,
            children=[],
            note=(
                "Parent header JSON did not contain a recognisable child list. "
                f"Top-level keys were: {sorted(payload.keys())}. Update "
                "parsers/children.py with the real shape."
            ),
        )

    active_id = _first_int(payload, _ACTIVE_ID_KEYS)
    active_org_id = _first_int(payload, _ACTIVE_ORG_ID_KEYS)
    children: list[Child] = []
    for entry in students_raw:
        if not isinstance(entry, dict):
            continue
        student_id = _first_int(entry, _ID_KEYS)
        if student_id is None:
            continue
        name = _full_name(entry)
        primary_school = _primary_school(entry)
        is_active = active_id is not None and student_id == active_id
        org_id = _first_int(primary_school, _ORG_ID_KEYS) or _first_int(entry, _ORG_ID_KEYS)
        if org_id is None and is_active:
            org_id = active_org_id
        children.append(
            Child(
                student_id=student_id,
                name=name,
                school=_first_str(primary_school, _SCHOOL_KEYS)
                or _first_str(entry, _SCHOOL_KEYS),
                grade=_first_str(primary_school, _GRADE_KEYS)
                or _first_str(entry, _GRADE_KEYS),
                org_id=org_id,
                active=is_active,
            )
        )

    return ChildList(
        school=school,
        children=children,
        active_student_id=active_id,
        active_org_id=active_org_id,
    )


def _full_name(entry: dict[str, Any]) -> str:
    """Build a display name from firstName + lastName, or fall back to ``name``."""
    direct = _first_str(entry, _NAME_KEYS)
    if direct:
        return direct
    first = _first_str(entry, _FIRST_NAME_KEYS)
    last = _first_str(entry, _LAST_NAME_KEYS)
    return f"{first} {last}".strip()


def _primary_school(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the school the parent can actually see (or an empty dict).

    A child who changed schools keeps the old enrolment in ``schools``, so
    the first element isn't necessarily the live one. Prefer an active
    enrolment the parent has access to — its ``orgId`` is what
    ``set_active_child`` has to send.
    """
    schools = entry.get("schools")
    if not isinstance(schools, list):
        return {}
    candidates = [s for s in schools if isinstance(s, dict)]
    for school in candidates:
        if school.get("studentActive") is not False and school.get("parentAllowedAccess") is not False:
            return school
    return candidates[0] if candidates else {}


def _first_list(d: dict[str, Any], keys: tuple[str, ...]) -> list[Any] | None:
    for k in keys:
        value = d.get(k)
        if isinstance(value, list):
            return value
    return None


def _first_str(d: dict[str, Any], keys: tuple[str, ...]) -> str:
    for k in keys:
        value = d.get(k)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_int(d: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for k in keys:
        value = d.get(k)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None
