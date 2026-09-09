"""Tests for the subject-room parsers: bodies, week references, date windows."""

from __future__ import annotations

import datetime as dt

from schoolsoft_mcp.models import Lesson, PlanningPart
from schoolsoft_mcp.parsers import subjectrooms as sr

# The real Idrott planning that motivated this module: a term-long planning
# whose per-week lines are the only place the meeting point is written down.
IDROTT_BODY_HTML = (
    "<p><strong>Idrott och hälsa terminen</strong></p>\n<p></p>\n"
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
            "<tr><td><p>34-36</p></td><td><p>Kapitel 1</p></td></tr>"
            "</tbody></table>"
        )
        lines = sr.html_to_text(html).splitlines()
        assert "Vecka | Område" in lines
        # The header's "Vecka" is carried onto the row, because nothing on a
        # data row names a week the way prose does and the week lookup would
        # otherwise never match a table-formatted term plan.
        assert "v.34-36 | Kapitel 1" in lines

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
        assert sr.week_references("V. 35: repetition inför provet") == {35}
        assert sr.week_references("Vecka 34") == {34}

    def test_range_expands(self) -> None:
        assert sr.week_references("Vecka 34\u201336 Kapitel 1") == {34, 35, 36}
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
        body = "Vecka 34\u201336 Kapitel 1 ! R\u00e9vision"
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
                "activityId": 7001,
                "subject": "Idrott och hälsa",
                "groupNames": ["7"],
                "color": "#d9de2d",
                "access": False,
                "isSubjectRoom": True,
            },
            {"activityId": 6004, "subject": "Bild", "groupNames": ["7"], "color": ""},
        ]
        result = sr.parse_rooms(payload, school="yourschool")
        assert [r.subject for r in result.rooms] == ["Bild", "Idrott och hälsa"]
        assert result.rooms[1].activity_id == 7001
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
                "activityId": 7001,
                "planningTitle": "",
                "planningPartTitle": "Idrott och hälsa terminen",
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
                "assignmentId": 8804,
                "activityId": 6004,
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
                "title": "Idrott och hälsa terminen",
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


def _FakeLesson(
    day: str = "monday",
    start: str = "08:20",
    end: str = "09:20",
    subject: str = "",
    notes: str = "",
    room: str = "",
    teacher: str = "",
    is_break: bool = False,
) -> Lesson:
    """A real ``Lesson`` with the fields the join reads.

    Deliberately not a duck-typed stand-in: the join reads these attributes
    off the model the server actually passes it, and a hand-rolled double
    would keep passing after the model changed underneath it.
    """
    return Lesson(
        day=day,
        start=start,
        end=end,
        subject=subject,
        notes=notes,
        room=room,
        teacher=teacher,
        is_break=is_break,
    )


def _planning(**kw: object) -> PlanningPart:
    return PlanningPart(**kw)  # type: ignore[arg-type]


IDROTT_PLANNING = _planning(
    title="Idrott och hälsa terminen",
    subject="Idrott och hälsa",
    activity_id=7001,
    body=sr.html_to_text(IDROTT_BODY_HTML),
    week_lines=["v.38 Terränglöpning, samling vid spårcentralen 8:20."],
)


class TestActivityJoin:
    def test_schedule_abbreviation_joins_to_full_subject_name(self) -> None:
        # The schedule says "ID", the planning says "Idrott och hälsa".
        lesson = _FakeLesson(subject="ID")
        assert sr.activity_for_lesson(lesson, [IDROTT_PLANNING]) == 7001

    def test_notes_are_used_when_subject_is_a_code(self) -> None:
        lesson = _FakeLesson(subject="XX", notes="Idrott")
        assert sr.activity_for_lesson(lesson, [IDROTT_PLANNING]) == 7001

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
        assert out[0].planning_titles == ["Idrott och hälsa terminen"]

    def test_planning_without_week_lines_falls_back_to_the_body(self) -> None:
        slojd = _planning(
            title="Slöjd",
            subject="Slöjd",
            activity_id=7007,
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
            activity_id=7008,
            body="Samling vid busshållplatsen 08:00, ta med matsäck.",
        )
        lessons = sr.day_lessons([_FakeLesson(subject="Biologi")], [bio], "monday")
        notes = sr.preparation_notes(lessons, week=38)
        assert len(notes) == 1
        assert "Samling vid busshållplatsen" in notes[0]

    def test_ordinary_lesson_is_not_listed(self) -> None:
        ma = _planning(title="Matte", subject="Matematik", activity_id=7004, body="sid 24")
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
            title="Idrott och hälsa terminen",
            subject="Idrott och hälsa",
            activity_id=7001,
            body="v.34 Friidrott (samling vid klubbstugan)\nv.35 Innebandy",
            mentions_weeks=True,
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
        assert sr.prep_label("XY", ["Idrott och hälsa terminen"]) == "idrott"


class TestWeekLineHygiene:
    def test_bare_week_heading_is_not_content(self) -> None:
        body = "Vecka 37\u201339:\nKapitel 1 - att presentera sig\nVecka 37\u201339 | Kapitel 1 - att presentera sig"
        assert sr.lines_for_week(body, 37) == ["Vecka 37\u201339 | Kapitel 1 - att presentera sig"]

    def test_identical_lines_are_collapsed(self) -> None:
        body = "v.37 Catch the flag\nv.37 Catch the flag"
        assert sr.lines_for_week(body, 37) == ["v.37 Catch the flag"]


class TestActivityJoinPrecision:
    """The short subject code is lossy; a wrong planning is worse than none."""

    BILD = _planning(title="Terminsplanering", subject="Bild", activity_id=6004, body="x")
    BIOLOGI = _planning(title="Cellen", subject="Biologi", activity_id=7008, body="y")

    def test_bi_does_not_pick_up_bild(self) -> None:
        # "bild".startswith("bi") — a permissive prefix match hung the art
        # planning off the biology lesson on the live page.
        lesson = _FakeLesson(subject="BI", notes="Biologi")
        assert sr.activity_for_lesson(lesson, [self.BILD]) == -1

    def test_bi_still_finds_biologi(self) -> None:
        lesson = _FakeLesson(subject="BI", notes="Biologi")
        assert sr.activity_for_lesson(lesson, [self.BILD, self.BIOLOGI]) == 7008

    def test_bild_still_matches_bild(self) -> None:
        lesson = _FakeLesson(subject="Bild", notes="Bild")
        assert sr.activity_for_lesson(lesson, [self.BILD, self.BIOLOGI]) == 6004

    def test_id_still_matches_idrott_och_halsa_by_prefix(self) -> None:
        lesson = _FakeLesson(subject="ID", notes="Idrott")
        assert sr.activity_for_lesson(lesson, [IDROTT_PLANNING, self.BILD]) == 7001

    def test_ambiguous_prefix_refuses_to_guess(self) -> None:
        other = _planning(title="Idrottsdag", subject="Idrottslära", activity_id=999, body="z")
        lesson = _FakeLesson(subject="XX", notes="Idrott")
        assert sr.activity_for_lesson(lesson, [IDROTT_PLANNING, other]) == -1

    def test_code_is_not_retried_when_the_full_name_missed(self) -> None:
        # notes says Spanska, no Spanska planning exists; falling back to the
        # code "Sp" would match "Språkval" and attach someone else's plan.
        sprakval = _planning(title="Franska", subject="Språkval", activity_id=6005, body="q")
        lesson = _FakeLesson(subject="Sp", notes="Spanska")
        assert sr.activity_for_lesson(lesson, [sprakval]) == -1


class TestWeekLinesSurviveTruncation:
    """The week you asked about is as likely to be on page four as page one."""

    def _term_plan(self, weeks: range, target: int) -> str:
        rows = "".join(
            f"<p>v.{w} {'Orientering, samling vid klubbstugan' if w == target else 'Innebandy i hallen'}</p>"
            for w in weeks
        )
        return f"<div>{rows}</div>"

    def test_late_week_survives_a_small_body_limit(self) -> None:
        html = self._term_plan(range(34, 52), target=50)
        out = sr.parse_detail_view(
            {"title": "Idrott HT", "description": html},
            week=50,
            max_body_chars=200,
        )
        # The body is cut, and the marker says so...
        assert out["body"].endswith("…")
        assert "v.50" not in out["body"]
        # ...but the line for the requested week came out of the full text.
        assert out["week_lines"] == ["v.50 Orientering, samling vid klubbstugan"]

    def test_body_is_still_truncated_to_the_limit(self) -> None:
        out = sr.parse_detail_view(
            {"description": self._term_plan(range(34, 52), target=50)},
            week=50,
            max_body_chars=200,
        )
        assert len(out["body"]) <= 201  # 200 plus the ellipsis

    def test_mentions_weeks_is_computed_on_the_full_text(self) -> None:
        out = sr.parse_detail_view(
            {"description": self._term_plan(range(34, 52), target=50)},
            week=12,
            max_body_chars=50,
        )
        assert out["mentions_weeks"] is True
        assert out["week_lines"] == []


class TestTableTermPlans:
    """A term plan written as a table must still answer "what about v.36?"."""

    HTML = (
        "<table><tbody>"
        "<tr><th>Vecka</th><th>Innehåll</th></tr>"
        "<tr><td>34-36</td><td>Kapitel 1</td></tr>"
        "<tr><td>37</td><td>Terränglöpning, samling vid spårcentralen 8:20</td></tr>"
        "</tbody></table>"
    )

    def test_week_lines_come_out_of_a_table(self) -> None:
        body = sr.html_to_text(self.HTML)
        assert sr.lines_for_week(body, 37) == [
            "v.37 | Terränglöpning, samling vid spårcentralen 8:20"
        ]

    def test_a_row_range_covers_every_week_in_it(self) -> None:
        body = sr.html_to_text(self.HTML)
        assert sr.lines_for_week(body, 35) == ["v.34-36 | Kapitel 1"]

    def test_a_table_without_a_week_header_is_left_alone(self) -> None:
        html = (
            "<table><tbody>"
            "<tr><th>Moment</th><th>Betyg</th></tr>"
            "<tr><td>34</td><td>C</td></tr>"
            "</tbody></table>"
        )
        assert "v.34" not in sr.html_to_text(html)


class TestWeekOrganisedBodyIsNotReusedForOtherWeeks:
    """The opening of a week-by-week plan describes *some other* week."""

    TERM_PLAN = _planning(
        title="Idrott och hälsa terminen",
        subject="Idrott och hälsa",
        activity_id=7001,
        body="v.34 Friidrott (samling vid klubbstugan)\nv.35 Innebandy",
        mentions_weeks=True,
    )

    def test_no_body_fallback_when_the_plan_is_organised_by_week(self) -> None:
        out = sr.day_lessons([_FakeLesson(subject="ID")], [self.TERM_PLAN], "monday")
        assert out[0].plannings == []
        assert out[0].planning_titles == ["Idrott och hälsa terminen"]

    def test_another_weeks_meeting_point_never_reaches_prepare(self) -> None:
        lessons = sr.day_lessons([_FakeLesson(subject="ID")], [self.TERM_PLAN], "monday")
        notes = sr.preparation_notes(lessons, week=45)
        assert "klubbstugan" not in notes[0]
        assert "inget står om v.45" in notes[0]

    def test_prose_plannings_still_fall_back_to_the_body(self) -> None:
        prose = _planning(
            title="Slöjd",
            subject="Slöjd",
            activity_id=7007,
            body="Ni gör en skrivbordsförvaring.",
            mentions_weeks=False,
        )
        out = sr.day_lessons([_FakeLesson(subject="Slöjd")], [prose], "monday")
        assert out[0].plannings == ["Ni gör en skrivbordsförvaring."]


class TestJoinWithAnnotatedNotes:
    """``notes`` is free text: often the subject's name, often an annotation."""

    def test_an_annotation_does_not_block_an_exact_subject_match(self) -> None:
        # "Ombyte" is not a subject; the row's own subject is exact.
        lesson = _FakeLesson(subject="Idrott och hälsa", notes="Ombyte")
        assert sr.activity_for_lesson(lesson, [IDROTT_PLANNING]) == 7001

    def test_an_annotation_still_cannot_license_a_prefix_match(self) -> None:
        # Prefix matching is where the short code is dangerous, so a lesson
        # whose only full-name candidate is an annotation stays unmatched
        # rather than falling back to "BI" and picking up "Bild".
        bild = _planning(title="Terminsplanering", subject="Bild", activity_id=6004)
        lesson = _FakeLesson(subject="BI", notes="Diagnos")
        assert sr.activity_for_lesson(lesson, [bild]) == -1

    def test_the_matched_activity_id_is_reported_on_the_lesson(self) -> None:
        out = sr.day_lessons([_FakeLesson(subject="ID")], [IDROTT_PLANNING], "monday")
        assert out[0].activity_id == 7001

    def test_an_unmatched_lesson_reports_no_activity_id(self) -> None:
        out = sr.day_lessons([_FakeLesson(subject="MA")], [IDROTT_PLANNING], "monday")
        assert out[0].activity_id is None


class TestLooseDates:
    """News dates are "10 maj", so an ISO-only parser filters nothing."""

    NEAR = dt.date(2026, 9, 9)

    def test_iso_dates_still_parse(self) -> None:
        assert sr.parse_loose_date("2026-05-10", near=self.NEAR) == dt.date(2026, 5, 10)

    def test_short_swedish_dates_parse(self) -> None:
        assert sr.parse_loose_date("10 maj", near=self.NEAR) == dt.date(2026, 5, 10)

    def test_the_year_chosen_is_the_nearest_one(self) -> None:
        # A December item read in January belongs to the year just gone.
        assert sr.parse_loose_date("20 dec", near=dt.date(2027, 1, 8)) == dt.date(
            2026, 12, 20
        )

    def test_junk_is_still_none(self) -> None:
        assert sr.parse_loose_date("i förrgår", near=self.NEAR) is None
        assert sr.parse_loose_date(None, near=self.NEAR) is None


class TestSubtitleIsSplitNotAliased:
    """``date_range`` is the date part, not a second copy of the subtitle."""

    def test_date_range_excludes_the_kind_and_subject(self) -> None:
        out = sr.parse_detail_view(
            {
                "title": "Terminsplanering",
                "subTitle": "tors 08 jan. - tors 18 juni Planering, Bild",
                "description": "<p>x</p>",
            }
        )
        assert out["subtitle"] == "tors 08 jan. - tors 18 juni Planering, Bild"
        assert out["date_range"] == "tors 08 jan. - tors 18 juni"
        assert out["kind"] == "Planering"
        assert out["subject"] == "Bild"

    def test_the_payloads_own_fields_win(self) -> None:
        out = sr.parse_detail_view(
            {
                "subTitle": "ons 13 maj, Bild",
                "type": "Diagnos",
                "subjectNames": "Biologi",
                "description": "",
            }
        )
        assert out["kind"] == "Diagnos"
        assert out["subject"] == "Biologi"
        assert out["date_range"] == "ons 13 maj"


class TestWeekBoundsValidation:
    def test_a_real_week_resolves(self) -> None:
        first, last = sr.week_bounds(2026, 37)
        assert first == dt.date(2026, 9, 7)
        assert last == dt.date(2026, 9, 13)

    def test_an_impossible_week_names_the_argument(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="week must be a valid ISO week"):
            sr.week_bounds(2026, 54)


class TestSharedFieldAccessors:
    """One implementation, so the same payload parses the same everywhere."""

    def test_every_parser_uses_the_same_int_rule(self) -> None:
        from schoolsoft_mcp.parsers import homework, schedule
        from schoolsoft_mcp.parsers._fields import int_field

        entry = {"a": "-5", "b": True, "c": "12", "d": 7, "e": "x"}
        for key in entry:
            expected = int_field(entry, key)
            assert homework._int_field(entry, key) == expected
            assert schedule._int_field(entry, key) == expected
            assert sr._i(entry, key) == expected

    def test_a_bool_is_not_an_int(self) -> None:
        from schoolsoft_mcp.parsers._fields import int_field

        assert int_field({"x": True}, "x") is None

    def test_the_iso_regex_still_captures_the_whole_date(self) -> None:
        # Several call sites read .group(1); a regex capturing the parts
        # separately would hand them the year instead.
        from schoolsoft_mcp.parsers._fields import ISO_DATE_RE

        assert ISO_DATE_RE.search("klart 2026-09-14 15:20").group(1) == "2026-09-14"

    def test_a_five_digit_year_is_not_a_date(self) -> None:
        from schoolsoft_mcp.parsers._fields import iso_date

        assert iso_date("12026-09-14") is None


class TestMentionsWeeksReachesTheModel:
    """The flag is useless if the wiring drops it on the way out.

    Found on a live page: ``parse_detail_view`` computed it, ``day_lessons``
    read it, and the ``PlanningPart`` built in ``get_planning`` never carried
    it — so every consumer of the listing saw False and printed some other
    week's body.
    """

    VIEW_KEYS = ("body", "week_lines", "mentions_weeks", "date_range")

    def test_parse_detail_view_supplies_every_key_the_model_needs(self) -> None:
        out = sr.parse_detail_view(
            {"description": "<p>V. 35: repetition</p>", "subTitle": "x, Planering, SO"},
            week=37,
        )
        for key in self.VIEW_KEYS:
            assert key in out, key
        assert out["mentions_weeks"] is True
        assert out["week_lines"] == []

    def test_the_flag_survives_a_round_trip_through_planning_part(self) -> None:
        view = sr.parse_detail_view(
            {"description": "<p>V. 35: repetition</p>"}, week=37
        )
        part = PlanningPart(
            title="Samhällets uppbyggnad",
            body=view["body"],
            week_lines=view["week_lines"],
            mentions_weeks=bool(view["mentions_weeks"]),
            activity_id=7006,
            subject="Historia",
        )
        assert part.mentions_weeks is True
        out = sr.day_lessons([_FakeLesson(subject="Historia")], [part], "monday")
        assert out[0].plannings == []
