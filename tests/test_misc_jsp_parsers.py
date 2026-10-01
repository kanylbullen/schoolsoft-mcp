"""Tests for the small JSP parsers: school info, contacts, library files."""

from __future__ import annotations

from pathlib import Path

import pytest

from schoolsoft_mcp.parsers.misc_jsp import (
    parse_contacts,
    parse_library_files,
    parse_school_info,
    parse_staff,
)

FIXTURES = Path(__file__).parent / "fixtures"


# --- school info ------------------------------------------------------------


@pytest.fixture
def school_info_html() -> str:
    return (FIXTURES / "school_info.html").read_text(encoding="utf-8")


def test_school_info_extracts_text(school_info_html: str) -> None:
    info = parse_school_info(school_info_html, school="X")
    assert info.note is None
    assert "Skolinformation" in info.text
    # Labels and values may end up on separate lines due to <strong> tags;
    # both must be present, but we don't constrain how they're glued.
    assert "Skolexpedition:" in info.text
    assert "010-123 45 67" in info.text
    assert "FÖRSÄKRINGAR" in info.text
    assert "Schoolyard 12" in info.text


def test_school_info_strips_scripts_and_styles(school_info_html: str) -> None:
    info = parse_school_info(school_info_html, school="X")
    assert "var x" not in info.text
    assert "color: red" not in info.text


def test_school_info_drops_inactivity_footer(school_info_html: str) -> None:
    info = parse_school_info(school_info_html, school="X")
    assert "Varning" not in info.text
    assert "inaktiv" not in info.text


def test_school_info_empty_html_returns_note() -> None:
    info = parse_school_info("<html><body></body></html>", school="X")
    assert info.text == ""
    assert info.note is not None


# --- contacts ---------------------------------------------------------------


@pytest.fixture
def contacts_html() -> str:
    return (FIXTURES / "contactlist.html").read_text(encoding="utf-8")


def test_contacts_parses_both_sides_per_row(contacts_html: str) -> None:
    cl = parse_contacts(contacts_html, school="X")
    names = [c.name for c in cl.contacts]
    assert names == ["Alice Andersson", "Bob Berg", "Cecilia Cederberg"]


def test_contacts_phone_and_address_extracted(contacts_html: str) -> None:
    cl = parse_contacts(contacts_html, school="X")
    by_name = {c.name: c for c in cl.contacts}
    alice = by_name["Alice Andersson"]
    assert alice.phone.startswith("070-000 00 01")
    assert "Storgatan 1" in alice.address


def test_contacts_handles_missing_fields(contacts_html: str) -> None:
    """Bob has no phone, Cecilia has no address. Missing fields default to ''."""
    cl = parse_contacts(contacts_html, school="X")
    by_name = {c.name: c for c in cl.contacts}
    assert by_name["Bob Berg"].phone == ""
    assert by_name["Cecilia Cederberg"].address == ""


def test_contacts_nbsp_normalised(contacts_html: str) -> None:
    """``&nbsp;`` between postcode and city becomes a regular space."""
    cl = parse_contacts(contacts_html, school="X")
    alice = next(c for c in cl.contacts if c.name == "Alice Andersson")
    assert "\xa0" not in alice.address
    assert " " in alice.address


def test_contacts_empty_returns_note() -> None:
    cl = parse_contacts("<html><body></body></html>", school="X")
    assert cl.contacts == []
    assert cl.note is not None


# --- library files ----------------------------------------------------------


@pytest.fixture
def library_html() -> str:
    return (FIXTURES / "library.html").read_text(encoding="utf-8")


def test_library_parses_all_files(library_html: str) -> None:
    lf = parse_library_files(library_html, school="X")
    assert lf.note is None
    assert [f.title for f in lf.files] == [
        "AI-policy",
        "Kränkning, anmälan",
        "Intyg simning",
    ]


def test_library_extracts_request_id_and_filename(library_html: str) -> None:
    lf = parse_library_files(library_html, school="X")
    by_title = {f.title: f for f in lf.files}
    assert by_title["AI-policy"].request_id == 100
    assert by_title["AI-policy"].filename == "Policy för användning av AI.pdf"


def test_library_extracts_description_only_when_present(library_html: str) -> None:
    lf = parse_library_files(library_html, school="X")
    by_title = {f.title: f for f in lf.files}
    assert by_title["Kränkning, anmälan"].description.startswith(
        "Anmälan till rektor"
    )
    assert by_title["AI-policy"].description == ""


def test_library_parses_size_with_nbsp(library_html: str) -> None:
    lf = parse_library_files(library_html, school="X")
    by_title = {f.title: f for f in lf.files}
    assert by_title["AI-policy"].size_bytes == 87 * 1024
    assert by_title["Intyg simning"].size_bytes == 28 * 1024


def test_library_assigns_category_from_preceding_heading(library_html: str) -> None:
    lf = parse_library_files(library_html, school="X")
    by_title = {f.title: f for f in lf.files}
    assert by_title["AI-policy"].category == "Policies"
    assert by_title["Intyg simning"].category == "Blanketter"


def test_library_content_type_guessed_from_filename(library_html: str) -> None:
    lf = parse_library_files(library_html, school="X")
    by_title = {f.title: f for f in lf.files}
    assert by_title["AI-policy"].content_type == "application/pdf"
    assert (
        by_title["Intyg simning"].content_type
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_library_empty_returns_note() -> None:
    lf = parse_library_files("<html><body></body></html>", school="X")
    assert lf.files == []
    assert lf.note is not None


# ----------------------------------------------------------------------------
# Personallista — teachers' e-mail lives here and nowhere else.
# ----------------------------------------------------------------------------


@pytest.fixture
def staff_html() -> str:
    return (FIXTURES / "staff.html").read_text(encoding="utf-8")


def test_staff_name_is_first_then_last(staff_html: str) -> None:
    """The page bolds the surname first; the schedule writes "Förnamn Efternamn"."""
    sl = parse_staff(staff_html, school="X")
    assert [m.name for m in sl.staff] == ["Alice Andersson", "Bo Berg Lind", "Cecilia Cederberg"]
    bo = sl.staff[1]
    assert (bo.first_name, bo.last_name) == ("Bo", "Berg Lind")


def test_staff_email_from_mailto_or_text(staff_html: str) -> None:
    by_name = {m.name: m for m in parse_staff(staff_html, school="X").staff}
    assert by_name["Alice Andersson"].email == "alice.andersson@example.test"
    assert by_name["Cecilia Cederberg"].email == "cecilia@example.test"


def test_staff_roles_split_on_br(staff_html: str) -> None:
    by_name = {m.name: m for m in parse_staff(staff_html, school="X").staff}
    assert by_name["Cecilia Cederberg"].roles == ["Biträdande rektor", "Lärare"]


def test_staff_listed_in_two_sections_is_merged(staff_html: str) -> None:
    """A mentor who also teaches appears under both headings, once without roles."""
    sl = parse_staff(staff_html, school="X")
    alice = [m for m in sl.staff if m.name == "Alice Andersson"]
    assert len(alice) == 1
    assert alice[0].groups == ["Mentorer", "Lärare"]
    assert alice[0].roles == ["Lärare"]
    assert alice[0].phone == "070-000 00 01"  # filled from the second listing
    assert len(sl.staff) == 3


def test_staff_groups_come_from_section_headings(staff_html: str) -> None:
    by_name = {m.name: m for m in parse_staff(staff_html, school="X").staff}
    assert by_name["Bo Berg Lind"].groups == ["Lärare"]
    assert by_name["Cecilia Cederberg"].groups == ["Skolledare"]


def test_staff_phone_and_contact_info(staff_html: str) -> None:
    by_name = {m.name: m for m in parse_staff(staff_html, school="X").staff}
    assert by_name["Alice Andersson"].phone == "070-000 00 01"
    assert by_name["Bo Berg Lind"].phone == ""
    assert by_name["Cecilia Cederberg"].contact_info == "Nås bäst förmiddagar"


def test_staff_unrecognised_layout_gives_note() -> None:
    sl = parse_staff("<html><table><tr><td>x</td></tr></table></html>", school="X")
    assert sl.staff == []
    assert sl.note and "dump_page" in sl.note
