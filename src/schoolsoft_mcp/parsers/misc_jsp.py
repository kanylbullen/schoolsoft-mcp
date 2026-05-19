"""Parsers for the small remaining JSP pages.

- ``right_student_school.jsp``  → ``SchoolInformation`` (free-form text)
- ``right_student_class.jsp``   → ``ContactList`` (classmate contacts)
- ``right_student_library.jsp`` → ``LibraryFileList`` (shared files)

These pages are simple HTML scrapes — no REST equivalents exist for
the typical SchoolSoft install we've observed. Each parser falls back
gracefully (empty list/string + descriptive ``note``) when the layout
doesn't match.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from ..models import (
    Contact,
    ContactList,
    LibraryFile,
    LibraryFileList,
    SchoolInformation,
)
from .attachments import guess_content_type

logger = logging.getLogger(__name__)

SCHOOL_INFO_PATHS = ("jsp/student/right_student_school.jsp",)
CONTACTS_PATHS = ("jsp/student/right_student_class.jsp",)
LIBRARY_PATHS = ("jsp/student/right_student_library.jsp",)

_SIZE_RE = re.compile(r"\(([\d.,]+)\s*(B|KB|MB|GB)\)", re.IGNORECASE)
_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}


# ----------------------------------------------------------------------------
# Skolinformation — free-form text extraction.
# ----------------------------------------------------------------------------


def parse_school_info(html: str, *, school: str) -> SchoolInformation:
    """Strip everything but visible body text.

    The page is CMS-edited free-form HTML (hours, phone numbers, term
    dates, addresses, …). Trying to impose structure would just lose
    information; we return the text and let the caller interpret it.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    container = soup.find("body") or soup
    text = container.get_text("\n", strip=True)
    # Collapse runs of blank lines but preserve paragraph breaks.
    cleaned = re.sub(r"\n{3,}", "\n\n", text)

    # Drop the inactivity-warning footer that appears on every JSP page.
    cleaned = re.sub(
        r"\n*Varning\s*\n+Du har varit inaktiv i:\s*\n+minuter\.?\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    note = None if cleaned.strip() else (
        "School info page produced no text. The HTML may have changed; "
        "call dump_page('jsp/student/right_student_school.jsp') to inspect."
    )
    return SchoolInformation(school=school, text=cleaned.strip(), note=note)


# ----------------------------------------------------------------------------
# Kontaktlistor — classmate contacts.
# ----------------------------------------------------------------------------


def parse_contacts(html: str, *, school: str) -> ContactList:
    """Each row of the contacts table contains two ``span6{left,right}`` divs,
    each holding one contact entry with id-marked sub-divs (``name``,
    ``phone``, ``address``).
    """
    soup = BeautifulSoup(html, "lxml")
    contacts: list[Contact] = []
    seen: set[tuple[str, str, str]] = set()

    for div in soup.find_all("div", class_=lambda c: bool(c) and "display-info" in c):
        if not isinstance(div, Tag):
            continue
        name = _id_text(div, "name")
        if not name:
            continue
        phone = _id_text(div, "phone")
        address = _id_text(div, "address")
        key = (name, phone, address)
        if key in seen:
            continue
        seen.add(key)
        contacts.append(Contact(name=name, phone=phone, address=address))

    note = None if contacts else (
        "No contacts parsed. Page layout may differ — call "
        "dump_page('jsp/student/right_student_class.jsp') to inspect."
    )
    return ContactList(school=school, contacts=contacts, note=note)


def _id_text(scope: Tag, element_id: str) -> str:
    """Return the text of the first element with the given ``id=`` inside ``scope``.

    The page uses the same id (e.g. ``name``) on every entry, which is
    invalid HTML but how SchoolSoft renders it. BeautifulSoup happily
    returns *all* matches; ``find(id=…)`` returns the first within scope.
    """
    el = scope.find(id=element_id)
    if isinstance(el, Tag):
        return el.get_text(" ", strip=True).replace("\xa0", " ")
    return ""


# ----------------------------------------------------------------------------
# Bibliotek — shared files.
# ----------------------------------------------------------------------------


def parse_library_files(html: str, *, school: str) -> LibraryFileList:
    """Each ``table-striped table-condensed`` table is one category section.

    Rows inside it have a ``<div class="heading_bold">title</div>``, an
    optional description ``<div>`` directly after, and a download
    ``<a href="right_student_library_download.jsp?requestid=N"
    title="<clean-filename>">…</a> (NNN KB)``.
    """
    soup = BeautifulSoup(html, "lxml")
    files: list[LibraryFile] = []

    for table in soup.find_all("table", class_=lambda c: bool(c) and "table-striped" in c):
        if not isinstance(table, Tag):
            continue
        category = _previous_section_heading(table)
        for row in table.find_all("tr"):
            if not isinstance(row, Tag):
                continue
            file = _library_row_to_file(row, category=category)
            if file is not None:
                files.append(file)

    note = None if files else (
        "No library files parsed. Page layout may differ — call "
        "dump_page('jsp/student/right_student_library.jsp') to inspect."
    )
    return LibraryFileList(school=school, files=files, note=note)


def _library_row_to_file(row: Tag, *, category: str) -> LibraryFile | None:
    title_div = row.find("div", class_="heading_bold")
    if not isinstance(title_div, Tag):
        return None
    title = title_div.get_text(" ", strip=True)
    if not title:
        return None

    description = ""
    # The description, if present, is the next sibling div after the
    # heading_bold one and before the <a>.
    sibling = title_div.find_next_sibling("div")
    if isinstance(sibling, Tag) and "heading_bold" not in (sibling.get("class") or []):
        description = sibling.get_text(" ", strip=True)

    anchor = row.find(
        "a", href=lambda h: bool(h) and "right_student_library_download.jsp" in h
    )
    if not isinstance(anchor, Tag):
        return None
    href = _href_of(anchor)
    qs = parse_qs(urlparse(href).query)
    request_id_raw = qs.get("requestid", [""])[0]
    request_id = int(request_id_raw) if request_id_raw.isdigit() else None

    title_attr = anchor.get("title", "")
    if isinstance(title_attr, list):
        title_attr = title_attr[0] if title_attr else ""
    filename = (title_attr or anchor.get_text(" ", strip=True)).strip()

    size_bytes = _extract_size(row)

    return LibraryFile(
        title=title,
        filename=filename,
        description=description,
        size_bytes=size_bytes,
        content_type=guess_content_type(filename),
        request_id=request_id,
        category=category,
    )


def _previous_section_heading(table: Tag) -> str:
    """Find the nearest preceding section heading for grouping context."""
    for candidate in table.find_all_previous(["h1", "h2", "h3", "h4", "div"]):
        if not isinstance(candidate, Tag):
            continue
        text = candidate.get_text(" ", strip=True)
        if not text or len(text) > 80:
            continue
        classes = candidate.get("class") or []
        if any(c in {"h3_bold", "heading", "section-header"} for c in classes):
            return text
        if candidate.name in {"h1", "h2", "h3", "h4"}:
            return text
    return ""


def _extract_size(row: Tag) -> int | None:
    """Pick up '(141 KB)' / '(1.5 MB)' size hints next to the filename."""
    text = row.get_text(" ", strip=True).replace("\xa0", " ")
    m = _SIZE_RE.search(text)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return int(value * _SIZE_UNITS[m.group(2).upper()])


def _href_of(tag: Tag) -> str:
    href = tag.get("href", "")
    if isinstance(href, list):
        return href[0] if href else ""
    return href
