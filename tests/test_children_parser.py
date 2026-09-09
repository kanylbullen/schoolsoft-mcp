"""Parent-header parsing: child identity, org IDs, and the active flag."""

from __future__ import annotations

from schoolsoft_mcp.parsers.children import parse_parent_header

HEADER_PAYLOAD = {
    "firstName": "Alex",
    "lastName": "Andersson",
    "children": [
        {
            "id": 900017,
            "firstName": "Bea",
            "lastName": "Andersson",
            "schools": [
                {
                    "orgId": 900003,
                    "className": "7B",
                    "schoolName": "Yourschool",
                    "studentActive": True,
                    "parentAllowedAccess": True,
                }
            ],
        },
        {
            "id": 4712,
            "firstName": "Cai",
            "lastName": "Andersson",
            "schools": [
                {
                    "orgId": 900003,
                    "className": "2A",
                    "schoolName": "Yourschool",
                    "studentActive": True,
                    "parentAllowedAccess": True,
                }
            ],
        },
    ],
    "currentChildId": 900017,
    "currentOrgId": 900003,
}


def test_parses_children_with_org_ids() -> None:
    result = parse_parent_header(HEADER_PAYLOAD, school="yourschool")
    assert [c.student_id for c in result.children] == [900017, 4712]
    assert [c.org_id for c in result.children] == [900003, 900003]
    assert result.active_student_id == 900017
    assert result.active_org_id == 900003


def test_marks_only_the_current_child_active() -> None:
    result = parse_parent_header(HEADER_PAYLOAD, school="yourschool")
    assert [c.active for c in result.children] == [True, False]


def test_names_and_class_labels() -> None:
    result = parse_parent_header(HEADER_PAYLOAD, school="yourschool")
    bea, cai = result.children
    assert bea.name == "Bea Andersson"
    assert bea.grade == "7B"
    assert cai.school == "Yourschool"


def test_prefers_the_enrolment_the_parent_can_see() -> None:
    """A child who changed schools keeps the stale enrolment first in the list."""
    payload = {
        "children": [
            {
                "id": 4713,
                "firstName": "Dre",
                "lastName": "Andersson",
                "schools": [
                    {
                        "orgId": 900002,
                        "className": "5C",
                        "schoolName": "Old School",
                        "studentActive": False,
                        "parentAllowedAccess": False,
                    },
                    {
                        "orgId": 900003,
                        "className": "6A",
                        "schoolName": "Yourschool",
                        "studentActive": True,
                        "parentAllowedAccess": True,
                    },
                ],
            }
        ],
        "currentChildId": 4713,
        "currentOrgId": 900003,
    }
    (child,) = parse_parent_header(payload, school="yourschool").children
    assert child.org_id == 900003
    assert child.grade == "6A"


def test_falls_back_to_current_org_for_the_active_child() -> None:
    """Older installs omit schools[]; currentOrgId still identifies the active one."""
    payload = {
        "children": [{"id": 900017, "name": "Bea"}, {"id": 4712, "name": "Cai"}],
        "currentChildId": 900017,
        "currentOrgId": 900003,
    }
    bea, cai = parse_parent_header(payload, school="yourschool").children
    assert bea.org_id == 900003
    assert cai.org_id is None


def test_unknown_shape_returns_a_note() -> None:
    result = parse_parent_header({"unexpected": True}, school="yourschool")
    assert result.children == []
    assert result.note is not None
