"""Parse the parent-header JSON into a :class:`ChildList`.

Observed shape of ``/rest-api/parent/header/parent``::

    {
      "firstName": "...",
      "lastName": "...",
      "children": [
        {
          "id": 999,
          "firstName": "...",
          "lastName": "...",
          "schools": [{"orgId": 1, "className": "6", "schoolName": "...",
                       "studentActive": true, "parentAllowedAccess": true}]
        },
        ...
      ],
      "currentChildId": 999,
      "currentOrgId": 1
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
    children: list[Child] = []
    for entry in students_raw:
        if not isinstance(entry, dict):
            continue
        student_id = _first_int(entry, _ID_KEYS)
        if student_id is None:
            continue
        name = _full_name(entry)
        primary_school = _primary_school(entry)
        children.append(
            Child(
                student_id=student_id,
                name=name,
                school=_first_str(primary_school, _SCHOOL_KEYS)
                or _first_str(entry, _SCHOOL_KEYS),
                grade=_first_str(primary_school, _GRADE_KEYS)
                or _first_str(entry, _GRADE_KEYS),
                active=(active_id is not None and student_id == active_id),
            )
        )

    return ChildList(school=school, children=children, active_student_id=active_id)


def _full_name(entry: dict[str, Any]) -> str:
    """Build a display name from firstName + lastName, or fall back to ``name``."""
    direct = _first_str(entry, _NAME_KEYS)
    if direct:
        return direct
    first = _first_str(entry, _FIRST_NAME_KEYS)
    last = _first_str(entry, _LAST_NAME_KEYS)
    return f"{first} {last}".strip()


def _primary_school(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the first entry in ``schools`` (or an empty dict)."""
    schools = entry.get("schools")
    if isinstance(schools, list) and schools and isinstance(schools[0], dict):
        return schools[0]
    return {}


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
