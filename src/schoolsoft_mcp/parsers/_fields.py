"""Reading fields out of SchoolSoft's JSON.

SchoolSoft is loose about types: an id arrives as ``12`` from one endpoint
and ``"12"`` from the next, a missing string is sometimes ``null`` and
sometimes absent. Every parser needs the same two accessors, and three
private copies had already drifted — one of them accepted ``"-5"`` and the
others did not, so the same payload parsed differently depending on which
tool asked.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

# ``2026-09-14``, anywhere in a longer string such as ``"2026-09-14 15:20"``.
# The word boundaries matter: without them ``12026-09-14`` parses. Group 1 is
# the whole date — several call sites read ``.group(1)`` and would silently
# get the year from a regex that captured the parts separately.
ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def str_field(entry: dict[str, Any], key: str) -> str:
    """``entry[key]`` as a stripped string, or ``""`` for anything else."""
    value = entry.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def int_field(entry: dict[str, Any], key: str) -> int | None:
    """``entry[key]`` as an int, or None. ``True`` is not 1 here."""
    value = entry.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def iso_date_str(raw: str | None) -> str:
    """The first ``YYYY-MM-DD`` in ``raw``, or ``""``."""
    if not raw:
        return ""
    m = ISO_DATE_RE.search(raw)
    return m.group(0) if m else ""


def iso_date(raw: str | None) -> dt.date | None:
    """The first ``YYYY-MM-DD`` in ``raw`` as a date, or None."""
    if not raw:
        return None
    m = ISO_DATE_RE.search(raw)
    if not m:
        return None
    try:
        return dt.date.fromisoformat(m.group(1))
    except ValueError:  # 2026-02-30 and friends
        return None
