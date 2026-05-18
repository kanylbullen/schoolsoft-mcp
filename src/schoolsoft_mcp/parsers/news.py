"""News and messages parsers.

News (incl. veckobrev) lives at ``jsp/student/right_student_news.jsp``.
Real markup (observed 2026-05) renders every item inline on the list
page inside a Bootstrap-style accordion::

    <div class="h3_bold">VECKOBREV</div>             <!-- section heading -->
    <div class="accordion-group" id="accordion-group88888">
      <div class="accordion-heading">
        <a class="accordion-toggle" href="#collapse88888">
          <span id="name88888">Veckobrev v 19-21 åk 6</span>
          <div class="preview-block">Info om ...</div>
          <div class="accordion-heading-date-wide">8 maj</div>
        </a>
      </div>
      <div id="collapse88888" class="accordion-body">
        <span id="description88888">
          <p class="tinymce-p">Info om kommande utflykt...</p>
        </span>
        <div class="accordion_inner_right">
          <label>Från</label><div>Alice Andersson (P)</div>
          <label>Till</label><div>6</div>
          <label>Publicerad</label><div>8 maj</div>
          <label>Visa till</label><div>23 maj</div>
          <div id="fileAttach88888">
            <a href="right_student_file_download.jsp?requestid1=88888
                     &requestid2=1&object=news&fileid=77777"
               title="Veckobrev år 6 v 19-21.pdf">Veckobre...pdf</a> (141 KB)
          </div>
        </div>
      </div>
    </div>

The news_id is the suffix on every per-item id (``name88888``,
``description88888``, ``fileAttach88888``…). The same id appears as
``requestid1`` on attachment links. ``type_id`` = ``requestid2`` (1 for
current, 2 for older).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from ..models import Attachment, Message, MessageList, NewsFeed, NewsItem
from .attachments import guess_content_type

logger = logging.getLogger(__name__)


def _href_of(tag: Tag) -> str:
    href = tag.get("href", "")
    if isinstance(href, list):
        return href[0] if href else ""
    return href


NEWS_PATHS = (
    "jsp/student/right_student_news.jsp",
    "jsp/student/right_student_news_list.jsp",
    "jsp/student/right_student_startpage.jsp",
)
MESSAGES_PATHS = (
    "jsp/student/right_student_message.jsp",
    "jsp/student/right_student_messages.jsp",
    "jsp/student/right_student_message_list.jsp",
)

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_SHORT_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(jan|feb|mar|apr|maj|jun|jul|aug|sep|okt|nov|dec)\.?\b",
    re.IGNORECASE,
)
_GROUP_ID_RE = re.compile(r"^accordion-group(\d+)$")

_META_LABELS = {
    "från": "author",
    "fran": "author",
    "from": "author",
    "till": "recipient",
    "to": "recipient",
    "publicerad": "published",
    "published": "published",
    "visa till": "visible_until",
    "show until": "visible_until",
}


def parse_news(html: str, *, school: str, default_type_id: int = 1) -> NewsFeed:
    """Parse the news list page.

    ``default_type_id`` is the SchoolSoft ``type`` enum used as a fallback
    when an item's own ``<form action="...&type=N">`` doesn't reveal it:
    1 = current/aktuella (default), 2 = older/äldre. Callers fetching the
    older view should pass ``default_type_id=2``.
    """
    soup = BeautifulSoup(html, "lxml")

    items = _parse_accordion_groups(soup, default_type_id=default_type_id)
    note: str | None = None
    if not items:
        items = _parse_generic_headers(soup)
        if items:
            note = (
                "Falling back to generic header parser — page didn't contain "
                "accordion-group divs. Item IDs/attachments will be empty."
            )
    if not items:
        note = (
            "News parser found no items. Page layout may differ; run "
            "dump_page('jsp/student/right_student_news.jsp') and open an issue."
        )

    return NewsFeed(school=school, items=items, note=note)


def _parse_accordion_groups(
    soup: BeautifulSoup, *, default_type_id: int
) -> list[NewsItem]:
    """The real list-view format: every item is its own accordion-group."""
    out: list[NewsItem] = []
    for group in soup.find_all("div", class_="accordion-group"):
        if not isinstance(group, Tag):
            continue
        news_id = _group_news_id(group)
        if news_id is None:
            continue
        out.append(
            NewsItem(
                news_id=news_id,
                type_id=_extract_type_id(group, fallback=default_type_id),
                title=_text_by_id(group, f"name{news_id}"),
                body=_body_text(group, news_id),
                date=_extract_date(group),
                category=_nearest_section_heading(group),
                **_extract_meta_block(group),
                attachments=_extract_attachments(group, news_id=news_id),
            )
        )
    return out


def _extract_type_id(group: Tag, *, fallback: int) -> int:
    """Pull ``type`` from a nested ``<form action="...?type=N">`` if present."""
    for form in group.find_all("form"):
        if not isinstance(form, Tag):
            continue
        action = form.get("action", "")
        if isinstance(action, list):
            action = action[0] if action else ""
        if not isinstance(action, str) or "type=" not in action:
            continue
        qs = parse_qs(urlparse(action).query)
        raw = qs.get("type", [""])[0]
        if raw.isdigit():
            return int(raw)
    return fallback


def _group_news_id(group: Tag) -> int | None:
    """Extract <ID> from id='accordion-group<ID>'."""
    gid = group.get("id")
    if isinstance(gid, list):
        gid = gid[0] if gid else ""
    if not isinstance(gid, str):
        return None
    m = _GROUP_ID_RE.match(gid)
    if not m:
        return None
    return int(m.group(1))


def _text_by_id(scope: Tag, element_id: str) -> str:
    el = scope.find(id=element_id)
    if isinstance(el, Tag):
        return el.get_text(" ", strip=True)
    return ""


def _body_text(group: Tag, news_id: int) -> str:
    """Body is in <span id='description<ID>'>; preserve paragraph breaks."""
    desc = group.find(id=f"description{news_id}")
    if not isinstance(desc, Tag):
        return ""
    paragraphs = [
        p.get_text(" ", strip=True) for p in desc.find_all(["p", "div"])
    ]
    paragraphs = [p for p in paragraphs if p and p != "\xa0"]
    if paragraphs:
        return "\n\n".join(paragraphs)
    return desc.get_text(" ", strip=True)


def _extract_date(group: Tag) -> str:
    date_el = group.find("div", class_="accordion-heading-date-wide")
    if isinstance(date_el, Tag):
        text = date_el.get_text(" ", strip=True)
        if text:
            return _normalise_date(text)
    return _normalise_date(group.get_text(" ", strip=True))


def _normalise_date(text: str) -> str:
    iso = _DATE_RE.search(text)
    if iso:
        return iso.group(1)
    short = _SHORT_DATE_RE.search(text)
    if short:
        return f"{short.group(1)} {short.group(2).lower().rstrip('.')}"
    return ""


def _nearest_section_heading(group: Tag) -> str:
    """Find the most recent <div class='h3_bold'>SECTION</div> before this item."""
    node = group.find_previous("div", class_="h3_bold")
    if isinstance(node, Tag):
        return node.get_text(" ", strip=True)
    # Fallback: any uppercase short heading.
    for prev in group.find_all_previous(["h1", "h2", "h3", "h4", "div"]):
        if not isinstance(prev, Tag):
            continue
        text = prev.get_text(" ", strip=True)
        if 0 < len(text) <= 60 and text == text.upper() and any(c.isalpha() for c in text):
            return text
    return ""


def _extract_meta_block(group: Tag) -> dict[str, str]:
    """Pull Från/Till/Publicerad/Visa till from the right-side metadata column."""
    out: dict[str, str] = {
        "author": "",
        "recipient": "",
        "published": "",
        "visible_until": "",
    }
    block = group.find("div", class_="accordion_inner_right")
    if not isinstance(block, Tag):
        return out
    for label in block.find_all("label"):
        if not isinstance(label, Tag):
            continue
        key = _META_LABELS.get(label.get_text(" ", strip=True).lower().rstrip(":"))
        if not key:
            continue
        value_tag = label.find_next_sibling("div")
        if isinstance(value_tag, Tag):
            text = value_tag.get_text(" ", strip=True)
            if text and not out[key]:
                out[key] = text
    return out


def _extract_attachments(group: Tag, *, news_id: int) -> list[Attachment]:
    """Files live in <div id='fileAttach<ID>'>; anchor title= has the clean name."""
    block = group.find(id=f"fileAttach{news_id}")
    if not isinstance(block, Tag):
        return []
    out: list[Attachment] = []
    seen: set[int] = set()
    for anchor in block.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href = _href_of(anchor)
        if "right_student_file_download.jsp" not in href:
            continue
        qs = parse_qs(urlparse(href).query)
        fileid_raw = qs.get("fileid", [""])[0]
        if not fileid_raw.isdigit():
            continue
        fileid = int(fileid_raw)
        if fileid in seen:
            continue
        # Prefer the title attribute (clean filename) over the truncated link text.
        title_attr = anchor.get("title", "")
        if isinstance(title_attr, list):
            title_attr = title_attr[0] if title_attr else ""
        filename = (title_attr or anchor.get_text(" ", strip=True)).strip()
        out.append(
            Attachment(
                fileid=fileid,
                filename=filename,
                size_bytes=_extract_attachment_size(anchor),
                content_type=guess_content_type(filename),
            )
        )
        seen.add(fileid)
    return out


def _extract_attachment_size(anchor: Tag) -> int | None:
    """Pick up '(141 KB)' / '(1.5 MB)' hints next to the filename."""
    nearby = ""
    if isinstance(anchor.parent, Tag):
        nearby = anchor.parent.get_text(" ", strip=True)
    # SchoolSoft uses non-breaking spaces in size labels: "141\xa0KB".
    nearby = nearby.replace("\xa0", " ")
    m = re.search(r"\(([\d.,]+)\s*(B|KB|MB|GB)\)", nearby, re.IGNORECASE)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2).upper()
    multiplier = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}[unit]
    return int(value * multiplier)


def _parse_generic_headers(soup: BeautifulSoup) -> list[NewsItem]:
    items: list[NewsItem] = []
    for header in soup.find_all(["h2", "h3", "h4"]):
        title = header.get_text(" ", strip=True)
        if not title:
            continue
        body_parts: list[str] = []
        for sib in header.find_next_siblings():
            if sib.name in {"h2", "h3", "h4"}:
                break
            text = sib.get_text(" ", strip=True)
            if text:
                body_parts.append(text)
            if sum(len(p) for p in body_parts) > 2000:
                break
        items.append(NewsItem(title=title, body=" ".join(body_parts)))
    return items


def parse_messages(html: str, *, school: str) -> MessageList:
    soup = BeautifulSoup(html, "lxml")
    items: list[Message] = []

    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        for row in table.find_all("tr"):
            if not isinstance(row, Tag):
                continue
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if len(cells) < 2:
                continue
            non_empty = [c for c in cells if c]
            if not non_empty:
                continue
            unread = any(_cell_is_unread(c) for c in row.find_all("td"))
            items.append(
                Message(
                    subject=non_empty[-1] if non_empty else "",
                    sender=non_empty[0] if non_empty else "",
                    date=_find_first_date(non_empty),
                    unread=unread,
                )
            )

    note: str | None = None
    if not items:
        note = (
            "Messages parser found no entries. Use dump_page to inspect the "
            "real structure of your school's inbox."
        )
    return MessageList(school=school, items=items, note=note)


def _find_first_date(texts: list[str]) -> str:
    for t in texts:
        m = _DATE_RE.search(t)
        if m:
            return m.group(1)
    return ""


def _cell_is_unread(cell: Any) -> bool:
    """True if any of the cell's CSS classes contains 'unread'."""
    if not isinstance(cell, Tag):
        return False
    classes = cell.get("class")
    if isinstance(classes, str):
        classes = [classes]
    if not isinstance(classes, list):
        return False
    return any(isinstance(c, str) and "unread" in c.lower() for c in classes)


# `Iterator` was used by the previous parser version; keeping the import
# explicit so type-checkers don't see a stale reference.
_ = Iterator
