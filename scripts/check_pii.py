#!/usr/bin/env python3
"""Refuse to commit anything that looks like it came out of a live account.

This repository is public and it talks to a school system. The data on the
other side is children's school records, and the easy mistake — the one
this script exists because of — is pasting a real API response into a test
fixture because it was already on screen.

Three layers, because no single one catches everything:

1. **Shapes.** Personal numbers, emails, phone numbers, absolute URLs
   naming a school instance. Mechanical and needs no secrets.
2. **Reserved ids.** Every SchoolSoft record id inside ``tests/`` must be
   in a reserved range that no real tenant uses. A pasted payload brings
   its real ids along, and this catches that even when the surrounding
   text looks harmless. This is the layer that would have caught the leak
   that prompted the script.
3. **A private denylist.** Names of children, teachers, places and the
   school itself cannot live in a public repo, not even as a denylist, so
   they are read from a file outside it — ``$PII_DENYLIST_FILE``, or
   ``~/.config/schoolsoft-mcp/pii-denylist.txt``. Absent, that layer is
   skipped and the script says so rather than passing silently.

Run over the whole tree, or over named files (what the pre-commit hook
does). Exit 1 on any finding.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Reserved for invented data. Real SchoolSoft ids observed in the wild are
# far below this; anything here is unmistakably made up.
RESERVED_ID_MIN = 900_000
RESERVED_ID_MAX = 999_999

SCAN_SUFFIXES = {".py", ".md", ".html", ".json", ".yml", ".yaml", ".toml", ".txt"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", "htmlcov", "dist", "build", "discovery"}
# This file necessarily contains the patterns it looks for.
SKIP_FILES = {"scripts/check_pii.py"}

SHAPE_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "personnummer",
        re.compile(r"\b(?:19|20)?\d{6}[-+]?\d{4}\b"),
        "Looks like a Swedish personal number.",
    ),
    (
        "email",
        re.compile(
            r"\b[\w.+-]+@(?!example\.|test\.|users\.noreply\.github\.com)"
            r"[\w-]+\.[\w.-]+\b"
        ),
        "Real-looking email address.",
    ),
    (
        "phone",
        re.compile(r"(?<![\w.])(?:\+46|0)7[02369][\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}\b"),
        "Looks like a Swedish mobile number.",
    ),
    (
        "school-instance-url",
        re.compile(r"sms\.schoolsoft\.se/(?!<|\{|yourschool|school\b)[a-z0-9_-]{2,}"),
        "Absolute URL naming a specific school. Use <school> or yourschool.",
    ),
    (
        "outline-or-uuid",
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
        "UUID — document ids and account ids look like this.",
    ),
    (
        "discord-snowflake",
        re.compile(r"<@!?\d{17,20}>|\b\d{18,20}\b"),
        "Looks like a Discord id.",
    ),
]

# JSON and model keys whose values are SchoolSoft record ids.
ID_KEYS = (
    "activityId", "activity_id", "assignmentId", "assignment_id",
    "holisticAssessmentId", "assessment_id", "planningPartId", "part_id",
    "planningId", "planning_id", "lessonId", "lesson_id", "sectionId",
    "section_id", "studentId", "student_id", "orgId", "org_id", "newsId",
    "news_id", "fileId", "file_id", "entity_id",
)
ID_RE = re.compile(
    r"[\"']?(" + "|".join(ID_KEYS) + r")[\"']?\s*[:=]\s*(\d+)"
)


def _obviously_invented(number: str) -> bool:
    """True for the placeholder numbers we write on purpose.

    "070-000 00 01" is nobody's phone. Insisting on inventing something that
    also looks unreal to a regex would only teach the next author to add a
    noqa, which is how a check stops being read.
    """
    digits = re.sub(r"\D", "", number)
    return "0000" in digits or len(set(digits[-6:])) <= 2


def load_denylist() -> tuple[list[str], str | None]:
    """Terms that must never appear, read from outside the repository."""
    raw = os.environ.get("PII_DENYLIST", "")
    path = os.environ.get("PII_DENYLIST_FILE") or str(
        Path.home() / ".config" / "schoolsoft-mcp" / "pii-denylist.txt"
    )
    text = raw
    if not text and Path(path).is_file():
        text = Path(path).read_text(encoding="utf-8")
    if not text:
        return [], path
    terms = [
        line.strip().lower()
        for line in re.split(r"[\n,]", text)
        if line.strip() and not line.strip().startswith("#")
    ]
    return terms, None


def iter_files(argv: list[str]) -> list[Path]:
    if argv:
        return [Path(a).resolve() for a in argv if Path(a).is_file()]
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return out


def check(path: Path, denylist: list[str]) -> list[str]:
    rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
    if rel in SKIP_FILES or path.suffix not in SCAN_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    findings: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for name, pattern, why in SHAPE_RULES:
            match = pattern.search(line)
            if not match:
                continue
            if name == "phone" and _obviously_invented(match.group(0)):
                continue
            findings.append(f"{rel}:{number}: [{name}] {why} -> {match.group(0)!r}")
        for term in denylist:
            if term and term in low:
                # Never print the term: this output ends up in CI logs.
                findings.append(
                    f"{rel}:{number}: [denylist] matches a private denylist entry "
                    f"({len(term)} chars). Replace it with invented data."
                )
        if rel.startswith("tests/"):
            for key, value in ID_RE.findall(line):
                ident = int(value)
                if ident and not RESERVED_ID_MIN <= ident <= RESERVED_ID_MAX:
                    findings.append(
                        f"{rel}:{number}: [record-id] {key}={ident} is outside the "
                        f"reserved test range {RESERVED_ID_MIN}-{RESERVED_ID_MAX}. "
                        "Real ids arrive by pasting a live response."
                    )
    return findings


def main(argv: list[str]) -> int:
    denylist, missing_path = load_denylist()
    findings: list[str] = []
    for path in iter_files(argv):
        findings.extend(check(path, denylist))

    if missing_path:
        print(
            f"note: no private denylist found at {missing_path} — names, places "
            "and the school are NOT being checked. Create it (one term per "
            "line) to enable that layer.",
            file=sys.stderr,
        )
    if findings:
        print("Possible live-account data found:\n", file=sys.stderr)
        for line in findings:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nFixtures must be written by hand, never pasted from a real "
            "response. See AGENTS.md.",
            file=sys.stderr,
        )
        return 1
    print(f"check_pii: clean ({len(iter_files(argv))} files, {len(denylist)} denylist terms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
