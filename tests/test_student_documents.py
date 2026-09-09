"""Tests for the Elevdokument (IUP) parsers.

Fixtures are hand-written from the live pages' shape. Every id, title,
name, date and sentence is invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from schoolsoft_mcp.parsers import student_documents as sd

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def grid() -> list[dict]:
    return sd.parse_document_grid((FIXTURES / "iup_grid.html").read_text(encoding="utf-8"))


@pytest.fixture
def content() -> dict:
    return sd.parse_document_part((FIXTURES / "iup_content.html").read_text(encoding="utf-8"))


@pytest.fixture
def empty() -> dict:
    return sd.parse_document_part((FIXTURES / "iup_empty.html").read_text(encoding="utf-8"))


# --- The grid ------------------------------------------------------------------


class TestGrid:
    def test_one_entry_per_document_with_its_id(self, grid: list[dict]) -> None:
        assert [d["title"] for d in grid] == ["IUP VT 2026", "IUP HT 2025"]
        assert [d["doc_id"] for d in grid] == [900021, 900022]

    def test_the_archive_table_is_not_mistaken_for_documents(self, grid: list[dict]) -> None:
        # Its header row has no title link; a row there would be a document
        # with no id and no parts.
        assert all(d["doc_id"] for d in grid)

    def test_status_comes_from_the_image_filename(self, grid: list[dict]) -> None:
        parts = {p["column"]: p for p in grid[0]["parts"]}
        assert parts["BI"]["awaiting"] == ["staff"]
        assert parts["BI"]["filled_by"] == []
        assert parts["Allmänt omdöme"]["filled_by"] == ["pupil"]

    def test_a_cell_can_carry_several_roles(self, grid: list[dict]) -> None:
        ma = {p["column"]: p for p in grid[0]["parts"]}["MA"]
        assert ma["filled_by"] == ["staff"]
        assert ma["awaiting"] == ["guardian"]

    def test_each_cell_keeps_the_parameters_its_page_needs(self, grid: list[dict]) -> None:
        parts = {p["column"]: p for p in grid[0]["parts"]}
        assert (parts["Allmänt omdöme"]["part_type"], parts["Allmänt omdöme"]["subject_id"]) == (4, 0)
        assert (parts["BI"]["part_type"], parts["BI"]["subject_id"]) == (1, 900101)
        assert parts["Övrigt"]["part_type"] == 2

    def test_print_icons_are_not_parts(self, grid: list[dict]) -> None:
        # The last cell links to javascript:void(0) with a printer icon and
        # no status image. It is furniture, not a document part.
        assert len(grid[0]["parts"]) == 4

    def test_junk_html_is_empty(self) -> None:
        assert sd.parse_document_grid("<html><body></body></html>") == []


# --- A document part -----------------------------------------------------------


class TestPart:
    def test_title_and_part_come_from_the_heading(self, content: dict) -> None:
        assert content["title"] == "IUP VT 2026"
        assert content["part_label"] == "Andras omdöme"
        assert content["subject_label"] == ""  # the general part has no subject

    def test_one_block_per_role_with_dates_and_author(self, content: dict) -> None:
        blocks = content["blocks"]
        assert [b["role"] for b in blocks] == ["Elevens eget omdöme", "Vårdnadshavarens omdöme"]
        first = blocks[0]
        assert first["written"] == "2026-02-07"
        assert first["updated"] == "2026-03-02"
        assert first["updated_by"] == "Alex Andersson"

    def test_sections_keep_number_label_and_text(self, content: dict) -> None:
        sections = content["blocks"][0]["sections"]
        assert [s["number"] for s in sections] == [1, 2, 4]
        assert sections[0]["label"] == "Mina mål"
        # <br/> inside the text becomes a line break, not a glued sentence.
        assert sections[0]["text"] == "Svenska: skriva hela meningar.\nMatte: öva tiokompisarna."
        assert sections[2]["label"] == "Närvarande"

    def test_a_meta_line_without_an_update_still_yields_the_date(self, content: dict) -> None:
        second = content["blocks"][1]
        assert second["written"] == "2026-02-10"
        assert second["updated"] is None
        assert second["updated_by"] == ""
        assert second["sections"][0]["label"] == "Hemma"

    def test_the_holistic_assessment_link_is_not_content(self, content: dict) -> None:
        text = " ".join(s["text"] for b in content["blocks"] for s in b["sections"])
        assert "sammantagen" not in text.lower()

    def test_no_note_when_there_is_content(self, content: dict) -> None:
        assert content["note"] is None


class TestEmptyPart:
    def test_subject_label_and_title_still_parse(self, empty: dict) -> None:
        assert empty["subject_label"] == "BI"
        assert empty["title"] == "IUP VT 2026"

    def test_no_answers_is_said_plainly(self, empty: dict) -> None:
        assert empty["blocks"] == []
        assert empty["note"] == "This part has no answers yet."

    def test_an_unrecognised_page_points_at_dump_page(self) -> None:
        out = sd.parse_document_part("<html><body><div id='main'></div></body></html>")
        assert out["blocks"] == []
        assert out["note"] is not None and "dump_page" in out["note"]
