"""Elevdokument — the IUP and development-talk records.

``right_student_review.jsp`` is where the individual development plan
(IUP) lives: the goals agreed at the utvecklingssamtal, the method for
reaching them and who is responsible, how the child says they are doing,
and who was in the room. It is the record a guardian re-reads before the
next talk, and the one place a child's own words about school appear.

Two views:

- **The grid** (no parameters). One row per document ("IUP VT 2026"), one
  column per subject plus "Övrigt" and "Allmänt omdöme". Each cell links to
  a part of the document — ``action=edit&requestid=<doc>&type=<part>
  &subject=<n>`` — and carries one image per role whose filename says who
  has filled it in: ``teacher-tick.png``, ``student-cross.png``,
  ``parent-tick.png``. The page's own legend: a green tick is a filled-in
  answer, a red cross an answer not yet given. ``mentor.png`` and
  ``followup.png`` only appear in that legend.
- **A part** (``action=edit`` with the cell's parameters). Despite the
  name it renders read-only for a guardian. One ``table.table-striped``
  whose rows are: ``td.header`` opening a block for one role ("Elevens
  eget omdöme"), ``td.longlistheader`` with the date and who last updated
  it, then nested tables each holding one section as a ``td.value`` label
  ("1: Mina mål") and a ``td.background`` text. An empty part says "Det
  finns inga svar att visa".

The general assessment is ``type=4&subject=0`` and is where the actual
IUP text sits on the tenant this was written against; the per-subject
parts (``type=1``) were all unfilled there, so their content layout is
assumed to match and is parsed by the same rules.

Read only. The page name says edit; nothing here posts.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

DOCUMENTS_PATH = "jsp/student/right_student_review.jsp"

# Image filename prefix -> role, as the page's legend names them.
ROLE_BY_IMAGE: dict[str, str] = {
    "teacher": "staff",
    "student": "pupil",
    "parent": "guardian",
}
_STATUS_IMAGE = re.compile(r"(teacher|student|parent)-(tick|cross)\.png$")
_NO_ANSWERS = "det finns inga svar"
# "- (2026-02-07), senast uppdaterad av Alex Andersson (2026-03-02)"
_META = re.compile(
    r"\(?(\d{4}-\d{2}-\d{2})\)?\s*,?\s*senast uppdaterad av\s+(.*?)\s*\((\d{4}-\d{2}-\d{2})\)",
    re.IGNORECASE,
)
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_SECTION_LABEL = re.compile(r"^\s*(\d+)\s*:\s*(.*)$")


def _query(href: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(href).query).items() if v}


def _main(html: str) -> Tag:
    soup = BeautifulSoup(html, "lxml")
    root = soup.find(id="main")
    return root if isinstance(root, Tag) else soup


def parse_document_grid(html: str) -> list[dict[str, Any]]:
    """One dict per document, with a status cell per column."""
    root = _main(html)
    table = root.find("table")
    if not isinstance(table, Tag):
        return []
    rows = table.find_all("tr")
    if not rows:
        return []
    header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["td", "th"])]

    documents: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        title_link = cells[0].find("a", href=True)
        title = cells[0].get_text(" ", strip=True)
        if not title_link or not title:
            continue
        doc_id = _query(str(title_link["href"])).get("requestid", "")
        parts: list[dict[str, Any]] = []
        for index, cell in enumerate(cells[1:], start=1):
            link = cell.find("a", href=True)
            images = [str(i.get("src") or "") for i in cell.find_all("img")]
            statuses = {}
            for src in images:
                match = _STATUS_IMAGE.search(src)
                if match:
                    statuses[ROLE_BY_IMAGE[match.group(1)]] = match.group(2) == "tick"
            if not link or not statuses:
                continue  # print icons and padding cells
            query = _query(str(link["href"]))
            parts.append(
                {
                    "column": header[index] if index < len(header) else "",
                    "part_type": int(query["type"]) if query.get("type", "").isdigit() else None,
                    "subject_id": int(query["subject"]) if query.get("subject", "").isdigit() else None,
                    "filled_by": sorted(r for r, ok in statuses.items() if ok),
                    "awaiting": sorted(r for r, ok in statuses.items() if not ok),
                }
            )
        documents.append(
            {
                "doc_id": int(doc_id) if doc_id.isdigit() else None,
                "title": title,
                "parts": parts,
            }
        )
    return documents


def _section_from_table(table: Tag) -> dict[str, Any] | None:
    label_cell = table.find("td", class_="value")
    text_cell = table.find("td", class_="background")
    if not isinstance(label_cell, Tag):
        return None
    raw_label = label_cell.get_text(" ", strip=True).replace("\xa0", " ")
    match = _SECTION_LABEL.match(raw_label)
    label = match.group(2).strip() if match else raw_label.strip()
    number = int(match.group(1)) if match else None
    text = (
        text_cell.get_text("\n", strip=True).replace("\xa0", " ")
        if isinstance(text_cell, Tag)
        else ""
    )
    return {"number": number, "label": label, "text": text}


def parse_document_part(html: str) -> dict[str, Any]:
    """One part of a document: the role blocks and their sections."""
    root = _main(html)
    text = root.get_text(" ", strip=True)

    headings = [h.get_text(" ", strip=True) for h in root.find_all("div", class_="h2")]
    # ["Elevdokument - BI", "IUP VT 2026 - Andras omdöme"]
    subject_label = ""
    title = ""
    part_label = ""
    for heading in headings:
        if heading.lower().startswith("elevdokument"):
            # "Elevdokument - BI" names the subject; the general part renders
            # "Elevdokument - -", and a lone dash is not a subject.
            subject_label = heading.split("-", 1)[1].strip(" -") if "-" in heading else ""
        elif " - " in heading:
            title, part_label = (s.strip() for s in heading.rsplit(" - ", 1))
        elif heading:
            title = heading

    blocks: list[dict[str, Any]] = []
    table = root.find("table", class_="table-striped")
    if isinstance(table, Tag):
        current: dict[str, Any] | None = None
        for row in table.find_all("tr", recursive=False) or table.find_all("tr"):
            if row.find_parent("table") is not table:
                continue
            for cell in row.find_all("td", recursive=False):
                classes = cell.get("class") or []
                if "header" in classes:
                    current = {
                        "role": cell.get_text(" ", strip=True),
                        "written": None,
                        "updated": None,
                        "updated_by": "",
                        "sections": [],
                    }
                    blocks.append(current)
                elif "longlistheader" in classes and current is not None:
                    meta = cell.get_text(" ", strip=True).replace("\xa0", " ")
                    match = _META.search(meta)
                    if match:
                        current["written"] = match.group(1)
                        current["updated_by"] = match.group(2).strip()
                        current["updated"] = match.group(3)
                    else:
                        dates = _ISO.findall(meta)
                        current["written"] = dates[0] if dates else None
                        current["updated"] = dates[-1] if len(dates) > 1 else None
                else:
                    if current is None:
                        current = {
                            "role": "",
                            "written": None,
                            "updated": None,
                            "updated_by": "",
                            "sections": [],
                        }
                        blocks.append(current)
                    for nested in cell.find_all("table"):
                        section = _section_from_table(nested)
                        if section and (section["label"] or section["text"]):
                            current["sections"].append(section)

    note: str | None = None
    if not blocks:
        note = (
            "This part has no answers yet."
            if _NO_ANSWERS in text.lower()
            else "No content found on the page — call "
            f"dump_page('{DOCUMENTS_PATH}?action=edit&...') and share a sanitised excerpt."
        )
    return {
        "title": title,
        "part_label": part_label,
        "subject_label": subject_label,
        "blocks": blocks,
        "note": note,
    }
