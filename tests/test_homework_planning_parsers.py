"""Tests for the REST homework + planning parsers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from schoolsoft_mcp.parsers.homework import (
    parse_homework_json,
    parse_planning_json,
    split_subtitle,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def assignments_json() -> list[dict]:  # type: ignore[type-arg]
    return json.loads(
        (FIXTURES / "assignments_start_page.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def planning_json() -> list[dict]:  # type: ignore[type-arg]
    return json.loads(
        (FIXTURES / "planning_parts_start_page.json").read_text(encoding="utf-8")
    )


# --- subtitle splitter ------------------------------------------------------


def test_split_subtitle_three_parts() -> None:
    """Standard '<dates>, <kind>, <subject>' shape."""
    assert split_subtitle(
        "ons 13 maj 00:00 - ons 20 maj 00:00, Diagnos, Moderna språk"
    ) == (
        "ons 13 maj 00:00 - ons 20 maj 00:00",
        "Diagnos",
        "Moderna språk",
    )


def test_split_subtitle_handles_commas_in_date_range() -> None:
    """Extra commas in the date region should not eat the kind/subject split."""
    result = split_subtitle("ons, tors, Diagnos, Bild")
    # The last two parts are always kind + subject; everything before joins.
    assert result == ("ons, tors", "Diagnos", "Bild")


def test_split_subtitle_two_parts() -> None:
    assert split_subtitle("ons 13 maj, Bild") == ("ons 13 maj", "", "Bild")


def test_split_subtitle_empty() -> None:
    assert split_subtitle("") == ("", "", "")


# --- homework JSON parser ---------------------------------------------------


def test_homework_parses_all_items(assignments_json: list[dict]) -> None:  # type: ignore[type-arg]
    h = parse_homework_json(assignments_json, school="X", week=21, year=2026)
    assert h.note is None
    assert h.week == 21
    assert h.year == 2026
    assert [i.title for i in h.items] == ["Diagnos kap.8", "Reklamaffisch"]


def test_homework_extracts_structured_fields(assignments_json: list[dict]) -> None:  # type: ignore[type-arg]
    h = parse_homework_json(assignments_json, school="X")
    diagnos = next(i for i in h.items if i.title == "Diagnos kap.8")
    assert diagnos.assignment_id == 900007
    assert diagnos.activity_id == 900008
    assert diagnos.kind == "Diagnos"
    assert diagnos.subject == "Moderna språk inom ramen för språkval"
    assert diagnos.due == "2026-05-20"
    assert diagnos.read is True
    assert diagnos.submission_status == "NO_STATUS"
    assert diagnos.result_status == "NOT_REPORTED"
    # subtitle preserved verbatim
    assert "Diagnos" in diagnos.subtitle


def test_homework_legacy_description_mirrors_subtitle(
    assignments_json: list[dict],  # type: ignore[type-arg]
) -> None:
    """The legacy ``description`` field carries the same text as ``subtitle``."""
    h = parse_homework_json(assignments_json, school="X")
    for item in h.items:
        assert item.description == item.subtitle


def test_homework_empty_payload_returns_note() -> None:
    h = parse_homework_json([], school="X", week=10, year=2026)
    assert h.items == []
    assert h.note is not None
    assert "homework" in h.note.lower()


def test_homework_non_list_payload_does_not_crash() -> None:
    h = parse_homework_json({"unexpected": "shape"}, school="X")
    assert h.items == []
    assert h.note is not None


# --- planning JSON parser ---------------------------------------------------


def test_planning_parses_all_items(planning_json: list[dict]) -> None:  # type: ignore[type-arg]
    p = parse_planning_json(planning_json, school="X", week=21, year=2026)
    assert p.note is None
    assert len(p.items) == 3


def test_planning_extracts_structured_fields(planning_json: list[dict]) -> None:  # type: ignore[type-arg]
    p = parse_planning_json(planning_json, school="X")
    matte = next(i for i in p.items if "Matte" in i.title)
    assert matte.part_id == 900012
    assert matte.planning_id == 900015
    assert matte.activity_id == 900010
    assert matte.kind == "Planering"
    assert matte.subject == "Matematik"
    assert matte.date_range == "tors 08 jan. - tors 18 juni"
    assert matte.read is True


def test_planning_handles_unread_items(planning_json: list[dict]) -> None:  # type: ignore[type-arg]
    p = parse_planning_json(planning_json, school="X")
    unread = [i for i in p.items if not i.read]
    assert len(unread) == 1
    assert unread[0].title == "(del 1 20/5) v.21"


def test_planning_empty_payload_returns_note() -> None:
    p = parse_planning_json([], school="X")
    assert p.items == []
    assert p.note is not None
    assert "planning" in p.note.lower()
