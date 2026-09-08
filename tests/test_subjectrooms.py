"""Tests for the subject-room parsers: bodies, week references, date windows."""

from __future__ import annotations

import datetime as dt

from schoolsoft_mcp.parsers import subjectrooms as sr

# The real Idrott planning that motivated this module: a term-long planning
# whose per-week lines are the only place the meeting point is written down.
IDROTT_BODY_HTML = (
    "<p><strong>Idrott och hälsa HT</strong></p>\n<p></p>\n"
    "<p>v.34 Brännboll (samling vid omklädningsrummen)</p>\n"
    "<p>v.35 Brännboll (samling vid omklädningsrummen)</p>\n"
    "<p>v.36 Vikarie, möter upp vid klassrummet.</p>\n"
    "<p>v.37 Orientering (samling vid klubbstugan)</p>\n"
    "<p>v.38 Terränglöpning, samling vid spårcentralen 8:20. Jag cyklar från skolan 08:00 "
    "för de som vill ha sällskap.</p>"
)


class TestHtmlToText:
    def test_paragraphs_become_lines(self) -> None:
        text = sr.html_to_text(IDROTT_BODY_HTML)
        lines = text.splitlines()
        assert "v.37 Orientering (samling vid klubbstugan)" in lines
        assert "<p>" not in text

    def test_table_rows_stay_one_row_per_line(self) -> None:
        html = (
            "<table><tbody>"
            "<tr><th><p>Vecka</p></th><th><p>Område</p></th></tr>"
            "<tr><td><p>34-36</p></td><td><p>Bienvenue</p></td></tr>"
            "</tbody></table>"
        )
        lines = sr.html_to_text(html).splitlines()
        assert "Vecka | Område" in lines
        assert "34-36 | Bienvenue" in lines

    def test_link_targets_are_kept(self) -> None:
        html = '<p>Uttal: <a href="https://youtu.be/x">klippet</a></p>'
        assert "klippet (https://youtu.be/x)" in sr.html_to_text(html)

    def test_nbsp_collapses_to_space(self) -> None:
        assert sr.html_to_text("<p>ÅR 7,&nbsp;hösten&nbsp;26</p>") == "ÅR 7, hösten 26"

    def test_plain_text_passes_through(self) -> None:
        assert sr.html_to_text("Hösttermin - Franska") == "Hösttermin - Franska"

    def test_empty_input(self) -> None:
        assert sr.html_to_text("") == ""

    def test_truncation_marks_itself(self) -> None:
        out = sr.html_to_text("<p>" + "a" * 100 + "</p>", max_chars=10)
        assert out.endswith("…")
        assert len(out) <= 11


class TestWeekReferences:
    def test_single_week(self) -> None:
        assert sr.week_references("v.37 Catch the flag") == {37}

    def test_spelled_out(self) -> None:
        assert sr.week_references("V. 35: Eleverna har börjat") == {35}
        assert sr.week_references("Vecka 34") == {34}

    def test_range_expands(self) -> None:
        assert sr.week_references("Vecka 34\u201336 Bienvenue") == {34, 35, 36}
        assert sr.week_references("v.34-38 Skiss i tusch") == {34, 35, 36, 37, 38}

    def test_no_week_reference(self) -> None:
        assert sr.week_references("att kunna köpa glass, läsk ...") == set()

    def test_time_of_day_is_not_a_week(self) -> None:
        # "8:20" and "08:00" must not be read as week numbers.
        assert sr.week_references("Samling vid spårcentralen 8:20, avfärd 08:00") == set()

    def test_backwards_range_does_not_wrap_the_year(self) -> None:
        # A backwards pair is far more likely to be a score or a date than a
        # week range that wraps New Year; only the first number is taken.
        assert sr.week_references("v.8-2") == {8}

    def test_out_of_range_week_ignored(self) -> None:
        assert sr.week_references("v.99 something") == set()


class TestLinesForWeek:
    def test_picks_only_the_matching_line(self) -> None:
        body = sr.html_to_text(IDROTT_BODY_HTML)
        assert sr.lines_for_week(body, 37) == [
            "v.37 Orientering (samling vid klubbstugan)"
        ]

    def test_range_line_matches_every_week_it_covers(self) -> None:
        body = "Vecka 34\u201336 Bienvenue ! R\u00e9vision"
        assert sr.lines_for_week(body, 35) == [body]

    def test_no_match_returns_empty(self) -> None:
        body = sr.html_to_text(IDROTT_BODY_HTML)
        assert sr.lines_for_week(body, 45) == []

    def test_prose_body_has_no_week_structure(self) -> None:
        body = "Ni gör en skrivbordförvaring för tex pennor."
        assert sr.lines_for_week(body, 37) == []
        assert sr.mentions_any_week(body) is False


class TestDateWindows:
    def test_parse_iso_date_from_datetime_string(self) -> None:
        assert sr.parse_iso_date("2026-09-14 15:20") == dt.date(2026, 9, 14)

    def test_parse_iso_date_rejects_prose(self) -> None:
        assert sr.parse_iso_date("6 sep. 15:53") is None
        assert sr.parse_iso_date(None) is None

    def test_week_bounds(self) -> None:
        monday, sunday = sr.week_bounds(2026, 37)
        assert monday == dt.date(2026, 9, 7)
        assert sunday == dt.date(2026, 9, 13)

    def test_term_long_planning_overlaps_a_week_inside_it(self) -> None:
        first, last = sr.week_bounds(2026, 37)
        assert sr.overlaps(dt.date(2026, 8, 19), dt.date(2026, 12, 31), first, last)

    def test_planning_that_ended_is_excluded(self) -> None:
        first, last = sr.week_bounds(2026, 37)
        assert not sr.overlaps(dt.date(2026, 8, 25), dt.date(2026, 9, 3), first, last)

    def test_planning_that_has_not_started_is_excluded(self) -> None:
        first, last = sr.week_bounds(2026, 37)
        assert not sr.overlaps(dt.date(2026, 10, 1), dt.date(2026, 10, 5), first, last)

    def test_open_ended_planning_is_kept(self) -> None:
        # SchoolSoft leaves endDate empty on plannings with no stated end;
        # dropping those would hide exactly the term-long ones.
        first, last = sr.week_bounds(2026, 37)
        assert sr.overlaps(dt.date(2026, 8, 19), None, first, last)
        assert sr.overlaps(None, None, first, last)


class TestPayloadParsers:
    def test_rooms(self) -> None:
        payload = [
            {
                "activityId": 379,
                "subject": "Idrott och hälsa",
                "groupNames": ["7"],
                "color": "#d9de2d",
                "access": False,
                "isSubjectRoom": True,
            },
            {"activityId": 334, "subject": "Bild", "groupNames": ["7"], "color": ""},
        ]
        result = sr.parse_rooms(payload, school="yourschool")
        assert [r.subject for r in result.rooms] == ["Bild", "Idrott och hälsa"]
        assert result.rooms[1].activity_id == 379
        assert result.note is None

    def test_rooms_empty_explains_itself(self) -> None:
        result = sr.parse_rooms([], school="yourschool")
        assert result.rooms == []
        assert result.note is not None and "student_id" in result.note

    def test_teachers_deduplicates(self) -> None:
        payload = [
            {"firstName": "Kim", "lastName": "Larsson", "id": 122},
            {"firstName": "Kim", "lastName": "Larsson", "id": 122},
        ]
        assert sr.parse_teachers(payload) == ["Kim Larsson"]

    def test_planning_row(self) -> None:
        row = sr.parse_planning_row(
            {
                "planningPartId": 720,
                "planningId": 504,
                "activityId": 379,
                "planningTitle": "",
                "planningPartTitle": "Idrott och hälsa HT",
                "subjects": [{"name": "Idrott och hälsa", "color": "#d9de2d"}],
                "teacher": "Kim Larsson",
                "startDate": "2026-08-19",
                "endDate": "2026-12-31",
                "publishDate": "2026-08-17 14:11",
                "status": "ONGOING",
                "read": True,
            }
        )
        assert row["part_id"] == 720
        assert row["subject"] == "Idrott och hälsa"
        assert row["start_date"] == "2026-08-19"

    def test_assignment_row(self) -> None:
        row = sr.parse_assignment_row(
            {
                "assignmentId": 441,
                "activityId": 334,
                "title": "Skiss i tusch",
                "subjects": [{"name": "Bild", "color": "#d5a3ab"}],
                "assignmentType": "Arbete under lektionstid",
                "teacher": "Robin Ek",
                "startDate": "2026-09-14 14:30",
                "endDate": "2026-09-14 15:20",
                "submissionStatus": "NO_STATUS",
                "resultReportStatus": "NOT_REPORTED",
                "status": "ONGOING",
                "read": False,
            }
        )
        assert row["kind"] == "Arbete under lektionstid"
        assert sr.parse_iso_date(row["end_date"]) == dt.date(2026, 9, 14)

    def test_detail_view_extracts_week_lines(self) -> None:
        view = sr.parse_detail_view(
            {
                "title": "Idrott och hälsa HT",
                "description": IDROTT_BODY_HTML,
                "publishDate": "17 aug. 14:11",
                "subtitle": "onsdag 19 augusti 2026 - torsdag 31 december 2026",
            },
            week=37,
        )
        assert view["week_lines"] == ["v.37 Orientering (samling vid klubbstugan)"]
        assert view["publish_date"] == "17 aug. 14:11"

    def test_detail_view_without_week_has_no_week_lines(self) -> None:
        view = sr.parse_detail_view({"title": "x", "description": "<p>y</p>"})
        assert "week_lines" not in view

    def test_detail_view_of_garbage(self) -> None:
        assert sr.parse_detail_view(None) == {}

    def test_material_merges_files_and_links(self) -> None:
        material = sr.parse_material(
            [{"id": 3, "displayName": "instruktion.pdf"}],
            [{"id": 9, "displayName": "Kahoot", "url": "https://kahoot.it/x"}],
        )
        assert [m.kind for m in material] == ["file", "link"]
        assert material[1].url == "https://kahoot.it/x"

    def test_exam_schedule(self) -> None:
        result = sr.parse_exam_schedule(
            [
                {
                    "id": "442",
                    "name": "Prov fredag v. 39",
                    "entityId": 442,
                    "typeName": "Prov",
                    "startDate": "2026-09-07T00:00",
                    "endDate": "2026-09-26T00:00",
                }
            ],
            school="yourschool",
        )
        assert result.exams[0].exam_id == 442
        assert result.exams[0].kind == "Prov"

    def test_exam_schedule_empty(self) -> None:
        result = sr.parse_exam_schedule([], school="yourschool")
        assert result.note is not None


class TestPaths:
    def test_usertype_segment(self) -> None:
        assert sr.usertype_segment(2) == "parent"
        assert sr.usertype_segment(1) == "student"
        # An unknown usertype falls back to parent rather than building a
        # nonsense path that 404s with no explanation.
        assert sr.usertype_segment(9) == "parent"

    def test_path_fills_both_slots(self) -> None:
        assert (
            sr.path(sr.PLANNING_PART_VIEW, 2, part_id=720)
            == "rest-api/parent/ps/planning_parts/720/view"
        )
        assert sr.path(sr.ROOMS_ALL, 1) == "rest-api/student/ps/subjectroom/all"


class _FakeLesson:
    """Minimal stand-in for models.Lesson (only the fields the join reads)."""

    def __init__(
        self,
        day: str = "monday",
        start: str = "08:20",
        end: str = "09:20",
        subject: str = "",
        notes: str = "",
        room: str = "",
        teacher: str = "",
        is_break: bool = False,
    ) -> None:
        self.day = day
        self.start = start
        self.end = end
        self.subject = subject
        self.notes = notes
        self.room = room
        self.teacher = teacher
        self.is_break = is_break
        self.attendance_status = ""


def _planning(**kw: object) -> object:
    from schoolsoft_mcp.models import PlanningPart

    return PlanningPart(**kw)  # type: ignore[arg-type]


IDROTT_PLANNING = _planning(
    title="Idrott och hälsa HT",
    subject="Idrott och hälsa",
    activity_id=379,
    body=sr.html_to_text(IDROTT_BODY_HTML),
    week_lines=["v.38 Terränglöpning, samling vid spårcentralen 8:20."],
)


class TestActivityJoin:
    def test_schedule_abbreviation_joins_to_full_subject_name(self) -> None:
        # The schedule says "ID", the planning says "Idrott och hälsa".
        lesson = _FakeLesson(subject="ID")
        assert sr.activity_for_lesson(lesson, [IDROTT_PLANNING]) == 379

    def test_notes_are_used_when_subject_is_a_code(self) -> None:
        lesson = _FakeLesson(subject="XX", notes="Idrott")
        assert sr.activity_for_lesson(lesson, [IDROTT_PLANNING]) == 379

    def test_unrelated_subject_does_not_join(self) -> None:
        assert sr.activity_for_lesson(_FakeLesson(subject="MA"), [IDROTT_PLANNING]) == -1

    def test_empty_lesson_does_not_join(self) -> None:
        assert sr.activity_for_lesson(_FakeLesson(), [IDROTT_PLANNING]) == -1


class TestDayLessons:
    def test_only_the_requested_day_in_time_order(self) -> None:
        lessons = [
            _FakeLesson(day="tuesday", start="09:00", subject="MA"),
            _FakeLesson(day="monday", start="10:00", subject="SV"),
            _FakeLesson(day="monday", start="08:20", subject="ID"),
        ]
        out = sr.day_lessons(lessons, [IDROTT_PLANNING], "monday")
        assert [x.subject for x in out] == ["ID", "SV"]

    def test_planning_text_is_attached_to_the_matching_lesson(self) -> None:
        out = sr.day_lessons([_FakeLesson(subject="ID")], [IDROTT_PLANNING], "monday")
        assert out[0].plannings == ["v.38 Terränglöpning, samling vid spårcentralen 8:20."]
        assert out[0].planning_titles == ["Idrott och hälsa HT"]

    def test_planning_without_week_lines_falls_back_to_the_body(self) -> None:
        slojd = _planning(
            title="Slöjd",
            subject="Slöjd",
            activity_id=345,
            body="Ni gör en skrivbordförvaring för tex pennor.",
        )
        out = sr.day_lessons([_FakeLesson(subject="Slöjd")], [slojd], "monday")
        assert out[0].plannings == ["Ni gör en skrivbordförvaring för tex pennor."]


class TestPreparationNotes:
    def test_idrott_reaches_the_list_under_its_schedule_code(self) -> None:
        lessons = sr.day_lessons([_FakeLesson(subject="ID")], [IDROTT_PLANNING], "monday")
        notes = sr.preparation_notes(lessons, week=38)
        assert notes == [
            "ID 08:20\u201309:20: v.38 Terr\u00e4ngl\u00f6pning, samling vid sp\u00e5rcentralen 8:20."
        ]

    def test_meeting_point_surfaces_for_any_subject(self) -> None:
        # The general case of the Idrott problem: a teacher saying "samling"
        # in a subject nobody thought to special-case.
        bio = _planning(
            title="Fältstudie",
            subject="Biologi",
            activity_id=296,
            body="Samling vid busshållplatsen 08:00, ta med matsäck.",
        )
        lessons = sr.day_lessons([_FakeLesson(subject="Biologi")], [bio], "monday")
        notes = sr.preparation_notes(lessons, week=38)
        assert len(notes) == 1
        assert "Samling vid busshållplatsen" in notes[0]

    def test_ordinary_lesson_is_not_listed(self) -> None:
        ma = _planning(title="Matte", subject="Matematik", activity_id=431, body="sid 24")
        lessons = sr.day_lessons([_FakeLesson(subject="Matematik")], [ma], "monday")
        assert sr.preparation_notes(lessons, week=38) == []

    def test_prep_subject_with_no_planning_says_so(self) -> None:
        lessons = sr.day_lessons([_FakeLesson(subject="ID")], [], "monday")
        notes = sr.preparation_notes(lessons, week=38)
        assert "ingen planering publicerad" in notes[0]

    def test_prep_subject_whose_planning_skips_the_week_says_that_instead(self) -> None:
        # Distinct from "no planning at all" — the family should know a
        # planning exists before being told to go read the veckobrev.
        idrott_other_week = _planning(
            title="Idrott och hälsa HT",
            subject="Idrott och hälsa",
            activity_id=379,
            body="",
        )
        lessons = sr.day_lessons(
            [_FakeLesson(subject="ID")], [idrott_other_week], "monday"
        )
        notes = sr.preparation_notes(lessons, week=45)
        assert "planering finns" in notes[0]
        assert "inget står om v.45" in notes[0]

    def test_breaks_are_skipped(self) -> None:
        lessons = sr.day_lessons(
            [_FakeLesson(subject="ID", is_break=True)], [IDROTT_PLANNING], "monday"
        )
        assert sr.preparation_notes(lessons, week=38) == []

    def test_duplicate_entries_are_collapsed(self) -> None:
        # Group-split lessons produce two schedule rows for the same planning.
        lessons = sr.day_lessons(
            [_FakeLesson(subject="ID"), _FakeLesson(subject="ID")],
            [IDROTT_PLANNING],
            "monday",
        )
        assert len(sr.preparation_notes(lessons, week=38)) == 1


class TestPrepLabel:
    def test_short_code_matches_as_a_whole_token(self) -> None:
        assert sr.prep_label("ID", []) == "idrott"

    def test_bild_is_not_idrott(self) -> None:
        # "id" is a substring of "Bild"; matching on substrings would have
        # flagged every art lesson as needing a gym kit.
        assert sr.prep_label("Bild", []) is None

    def test_planning_title_can_supply_the_label(self) -> None:
        assert sr.prep_label("XY", ["Idrott och hälsa HT"]) == "idrott"


class TestWeekLineHygiene:
    def test_bare_week_heading_is_not_content(self) -> None:
        body = "Vecka 37\u201339:\nMoi et ma famille\nVecka 37\u201339 | Moi et ma famille"
        assert sr.lines_for_week(body, 37) == ["Vecka 37\u201339 | Moi et ma famille"]

    def test_identical_lines_are_collapsed(self) -> None:
        body = "v.37 Catch the flag\nv.37 Catch the flag"
        assert sr.lines_for_week(body, 37) == ["v.37 Catch the flag"]


class TestActivityJoinPrecision:
    """The short subject code is lossy; a wrong planning is worse than none."""

    BILD = _planning(title="Terminsplanering", subject="Bild", activity_id=334, body="x")
    BIOLOGI = _planning(title="Cellen", subject="Biologi", activity_id=296, body="y")

    def test_bi_does_not_pick_up_bild(self) -> None:
        # "bild".startswith("bi") — a permissive prefix match hung the art
        # planning off the biology lesson on the live page.
        lesson = _FakeLesson(subject="BI", notes="Biologi")
        assert sr.activity_for_lesson(lesson, [self.BILD]) == -1

    def test_bi_still_finds_biologi(self) -> None:
        lesson = _FakeLesson(subject="BI", notes="Biologi")
        assert sr.activity_for_lesson(lesson, [self.BILD, self.BIOLOGI]) == 296

    def test_bild_still_matches_bild(self) -> None:
        lesson = _FakeLesson(subject="Bild", notes="Bild")
        assert sr.activity_for_lesson(lesson, [self.BILD, self.BIOLOGI]) == 334

    def test_id_still_matches_idrott_och_halsa_by_prefix(self) -> None:
        lesson = _FakeLesson(subject="ID", notes="Idrott")
        assert sr.activity_for_lesson(lesson, [IDROTT_PLANNING, self.BILD]) == 379

    def test_ambiguous_prefix_refuses_to_guess(self) -> None:
        other = _planning(title="Idrottsdag", subject="Idrottslära", activity_id=999, body="z")
        lesson = _FakeLesson(subject="XX", notes="Idrott")
        assert sr.activity_for_lesson(lesson, [IDROTT_PLANNING, other]) == -1

    def test_code_is_not_retried_when_the_full_name_missed(self) -> None:
        # notes says Spanska, no Spanska planning exists; falling back to the
        # code "Sp" would match "Språkval" and attach someone else's plan.
        sprakval = _planning(title="Franska", subject="Språkval", activity_id=363, body="q")
        lesson = _FakeLesson(subject="Sp", notes="Spanska")
        assert sr.activity_for_lesson(lesson, [sprakval]) == -1
