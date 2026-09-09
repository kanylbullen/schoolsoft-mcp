"""Tests for Sammantagen bedömning, results and the open-work list.

Payload shapes are copied from a live parent account; names, subjects and
ids are invented.
"""

from __future__ import annotations

from typing import ClassVar

from schoolsoft_mcp.parsers import assessment as asm

ROWS = [
    {
        "title": "Bild",
        "subTitle": "Mer än godtagbara kunskaper",
        "color": "#d5a3ab",
        "subjectWarning": False,
        "updatedAt": "2026-05-28T13:28:30",
        "friendlyUpdatedAt": "28 maj 13:28",
        "publishedAt": "2026-05-28T13:28:30",
        "friendlyPublishedAt": "28 maj 13:28",
        "holisticAssessmentId": 5101,
        "published": True,
        "read": False,
    },
    {
        "title": "Matematik",
        "subTitle": "Godtagbara kunskaper",
        "subjectWarning": True,
        "updatedAt": "2026-06-01T09:00:00",
        "friendlyUpdatedAt": "1 juni 09:00",
        "publishedAt": "2026-06-01T09:00:00",
        "friendlyPublishedAt": "1 juni 09:00",
        "holisticAssessmentId": 5102,
        "published": True,
        "read": True,
    },
    {
        "title": "Hem- och konsumentkunskap",
        "subTitle": "Ingen bedömning",
        "subjectWarning": False,
        "holisticAssessmentId": 5103,
        "published": False,
        "read": True,
    },
]

OPTIONS = [
    {"id": "5101", "path": "#/parent/holistic_assessment/5101", "label": "Bild (BL)"},
    {"id": "5102", "path": "#/parent/holistic_assessment/5102", "label": "Matematik (MA)"},
    {"id": "9999", "path": "#/parent/holistic_assessment/9999", "label": "Utan kod"},
]


class TestAssessmentRows:
    def test_flagged_subjects_sort_first(self) -> None:
        # A family reading on a phone sees the top of the list. The subject
        # the school is worried about belongs there.
        out = asm.parse_assessment_rows(ROWS)
        assert out[0]["subject"] == "Matematik"
        assert out[0]["subject_warning"] is True

    def test_the_schools_own_wording_is_kept(self) -> None:
        out = {r["subject"]: r for r in asm.parse_assessment_rows(ROWS)}
        assert out["Bild"]["assessment"] == "Mer än godtagbara kunskaper"
        assert out["Hem- och konsumentkunskap"]["assessment"] == "Ingen bedömning"

    def test_subject_codes_are_merged_from_the_options_list(self) -> None:
        # The code is the form the schedule uses, and it appears nowhere on
        # the rows endpoint.
        out = {r["subject"]: r for r in asm.parse_assessment_rows(ROWS, OPTIONS)}
        assert out["Bild"]["subject_code"] == "BL"
        assert out["Matematik"]["subject_code"] == "MA"

    def test_a_missing_code_is_empty_not_wrong(self) -> None:
        out = {r["subject"]: r for r in asm.parse_assessment_rows(ROWS, None)}
        assert out["Bild"]["subject_code"] == ""

    def test_an_unparenthesised_label_yields_no_code(self) -> None:
        assert asm.subject_code("Utan kod") == ""
        assert asm.subject_code("Idrott och hälsa (IDH)") == "IDH"

    def test_read_and_published_flags_survive(self) -> None:
        out = {r["subject"]: r for r in asm.parse_assessment_rows(ROWS)}
        assert out["Bild"]["read"] is False
        assert out["Hem- och konsumentkunskap"]["published"] is False

    def test_junk_rows_are_skipped(self) -> None:
        assert asm.parse_assessment_rows(["x", None, 3]) == []
        assert asm.parse_assessment_rows(None) == []


class TestSubjectWarning:
    def test_an_unpublished_warning_is_marked_as_such(self) -> None:
        # The school flagging a subject internally is not the same as the
        # family having been told. Reporting the first as the second would
        # tell a parent they had been informed when they had not.
        out = asm.parse_subject_warning(
            {"active": True, "published": False, "comment": "Behöver stöd i taluppfattning."}
        )
        assert out["active"] is True
        assert out["published"] is False
        assert out["comment"] == "Behöver stöd i taluppfattning."

    def test_placeholder_dashes_become_empty(self) -> None:
        out = asm.parse_subject_warning({"active": False, "lastUpdatedAt": "-"})
        assert out["updated"] == ""

    def test_junk_is_inactive(self) -> None:
        assert asm.parse_subject_warning(None)["active"] is False


class TestKnowledgeDevelopment:
    def test_the_assessment_and_support_measures_come_out(self) -> None:
        out = asm.parse_knowledge_development(
            {
                "value": "Mer än godtagbara kunskaper",
                "supportMeasures": "Extra lästräning två gånger i veckan.",
                "updatedByInfo": "Senast uppdaterad 28 maj 13:28 av Alex Andersson",
            }
        )
        assert out["assessment"] == "Mer än godtagbara kunskaper"
        assert out["support_measures"].startswith("Extra lästräning")
        assert "Alex Andersson" in out["updated_by"]

    def test_junk_is_empty_strings(self) -> None:
        assert asm.parse_knowledge_development(None)["assessment"] == ""


class TestAssessedWork:
    WORK: ClassVar[list[dict[str, object]]] = [
        {
            "gridColumnInfo": {"isReview": True},
            "id": 8803,
            "activityId": 6003,
            "name": "Affisch i tuschteknik",
            "type": "Arbete under lektionstid",
            "review": "B",
            "points": "",
            "formativeComment": "Fin komposition.",
            "assessedCriteria": [],
        }
    ]

    def test_the_grade_comes_out_of_review(self) -> None:
        # This is the only place on the parent surface an actual grade
        # appears; the results list has no such field.
        out = asm.parse_assessed_work(self.WORK)
        assert out[0]["grade"] == "B"
        assert out[0]["title"] == "Affisch i tuschteknik"
        assert out[0]["comment"] == "Fin komposition."

    def test_junk_is_empty(self) -> None:
        assert asm.parse_assessed_work({"not": "a list"}) == []


class TestResults:
    ROWS: ClassVar[list[dict[str, object]]] = [
        {
            "assignmentId": 8801,
            "activityId": 6001,
            "title": "Prov om krafter",
            "subjects": [{"name": "Fysik", "color": "#985de6"}],
            "assignmentType": "Prov",
            "teacher": "Alex Andersson",
            "publishDate": "2025-09-17 22:46",
            "read": True,
        },
        {
            "assignmentId": 8802,
            "activityId": 6002,
            "title": "Matteläxa",
            "subjects": [{"name": "Matematik"}],
            "assignmentType": "Läxa",
            "teacher": "Alex Andersson",
            "publishDate": "2026-01-11 16:24",
            "read": False,
        },
    ]

    def test_newest_first(self) -> None:
        out = asm.parse_result_rows(self.ROWS)
        assert [r["title"] for r in out] == ["Matteläxa", "Prov om krafter"]

    def test_subjects_are_flattened(self) -> None:
        assert asm.parse_result_rows(self.ROWS)[0]["subject"] == "Matematik"

    def test_no_grade_field_is_invented(self) -> None:
        # An empty "grade" key would read as "no grade given" when the truth
        # is that this endpoint does not carry one.
        assert "grade" not in asm.parse_result_rows(self.ROWS)[0]


class TestOpenWork:
    ROWS: ClassVar[list[dict[str, object]]] = [
        {
            "id": 8804,
            "partId": None,
            "entityType": "ASSIGNMENT",
            "activityId": 6004,
            "title": "Formövning i tre delar",
            "type": "Arbete under lektionstid",
            "endDate": "2026-09-14",
            "endTime": "15:20",
            "submissionStatus": "NO_STATUS",
            "resultReportStatus": "NOT_REPORTED",
            "status": "ONGOING",
            "subjectNames": ["Bild"],
            "read": True,
        },
        {
            "id": 8805,
            "entityType": "ASSIGNMENT",
            "activityId": 6005,
            "title": "Förhör kapitel 1",
            "type": "Diagnos",
            "endDate": "2026-09-08",
            "endTime": "12:25",
            "status": "ONGOING",
            "subjectNames": ["Språkval"],
            "read": True,
        },
        {
            "id": 300,
            "entityType": "ASSIGNMENT",
            "title": "Gammal läxa",
            "endDate": None,
            "status": "FINISHED",
            "subjectNames": ["Svenska"],
        },
    ]

    def test_due_order(self) -> None:
        out = asm.parse_open_work(self.ROWS)
        assert [r["title"] for r in out][:2] == ["Förhör kapitel 1", "Formövning i tre delar"]

    def test_undated_work_sorts_last_rather_than_first(self) -> None:
        # A None end date sorting first would put "no deadline" at the top of
        # a list a family reads as "most urgent".
        assert asm.parse_open_work(self.ROWS)[-1]["title"] == "Gammal läxa"

    def test_subject_names_are_flattened_from_a_string_list(self) -> None:
        assert asm.parse_open_work(self.ROWS)[0]["subject"] == "Språkval"

    def test_status_is_preserved_so_the_caller_can_filter(self) -> None:
        statuses = {r["title"]: r["status"] for r in asm.parse_open_work(self.ROWS)}
        assert statuses["Gammal läxa"] == "FINISHED"


class TestHistory:
    def test_earlier_terms_come_out(self) -> None:
        out = asm.parse_history(
            [
                {"assessmentId": 5101, "current": True, "reportingOccasion": None},
                {
                    "assessmentId": 4161,
                    "current": False,
                    "reportingOccasion": {"occasion": "2025-12-30", "name": "HT 25"},
                },
            ]
        )
        assert out[0]["current"] is True and out[0]["term"] == ""
        assert out[1]["term"] == "HT 25"
        assert out[1]["occasion_date"] == "2025-12-30"


class TestPaths:
    def test_usertype_fills_the_segment(self) -> None:
        assert (
            asm.path_for(asm.ASSESSMENT_ROWS, 2)
            == "rest-api/parent/holistic_assessment/rows"
        )

    def test_extra_slots_fill_too(self) -> None:
        assert asm.path_for(asm.ASSESSMENT_WORK, 2, assessment_id=5101) == (
            "rest-api/parent/holistic_assessment/5101/assessed_assignments"
        )
