"""End-to-end smoke test against a real SchoolSoft account.

Runs every new client method and parser against the live API and prints
PASS/FAIL for each. Use before opening a PR to catch parser regressions
or broken assumptions that ruff/mypy/unit tests can't detect.

Output goes to stdout only — no files written, no PII saved. Long digit
runs (student IDs, file IDs) and the school slug are masked in any
detail strings included with each result; bodies of news items and
attachment text are deliberately never included.

Usage:
    phase run -- python scripts/smoke_test.py
    # or:
    python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import sys
import traceback
from dataclasses import dataclass
from typing import Any

from schoolsoft_mcp.client import SchoolSoftClient
from schoolsoft_mcp.config import Settings
from schoolsoft_mcp.parsers.attachments import (
    build_download_path,
    extract_text,
    filename_from_headers,
)
from schoolsoft_mcp.parsers.children import parse_parent_header
from schoolsoft_mcp.parsers.news import NEWS_PATHS, parse_news

logger = logging.getLogger("smoke")


@dataclass(slots=True)
class TestResult:
    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        # Run both name and detail through _redact at print time so IDs and
        # the school slug never leak, even when a caller forgot to redact
        # when constructing the strings.
        name = _redact(self.name, max_len=120)
        redacted = _redact(self.detail, max_len=200) if self.detail else ""
        return f"  [{mark}] {name}{' — ' + redacted if redacted else ''}"


_SCHOOL_SLUG: str = ""
# Mask digit runs of 3+ (student IDs are typically 3-4 digits, file IDs 5+),
# but skip 4-digit years like 2026 so the test output stays readable.
_DIGIT_RUN_RE = re.compile(r"\b(?!(?:19|20)\d{2}\b)\d{3,}\b")


def _redact(s: str, max_len: int = 80) -> str:
    """Truncate + redact long digit runs and the school slug.

    Long digit runs typically identify a student (3+ digits) or file ID
    (5+ digits); both are sensitive. The school slug appears in every
    URL and would deanonymise the test output by itself.
    """
    s = s.replace("\n", " ").strip()
    if _SCHOOL_SLUG and _SCHOOL_SLUG in s:
        s = s.replace(_SCHOOL_SLUG, "<school>")
    s = _DIGIT_RUN_RE.sub("<id>", s)
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


async def _check(
    label: str, coro: Any, validate: Any = None
) -> TestResult:
    try:
        result = await coro
    except Exception as err:
        return TestResult(label, False, f"raised {type(err).__name__}: {err}")
    if validate is not None:
        try:
            detail = validate(result)
        except AssertionError as err:
            return TestResult(label, False, f"validation failed: {err}")
        return TestResult(label, True, detail or "")
    return TestResult(label, True, "")


async def _run() -> int:
    settings = Settings.from_env()
    global _SCHOOL_SLUG
    _SCHOOL_SLUG = settings.school
    iso_year, iso_week = dt.date.today().isocalendar()[:2]
    results: list[TestResult] = []

    print(f"Smoke test against school={settings.school!r} (week {iso_week}/{iso_year})")
    print()

    async with SchoolSoftClient(
        school=settings.school,
        username=settings.username,
        password=settings.password,
        usertype=settings.usertype,
        base_url=settings.base_url,
        timeout=settings.request_timeout,
    ) as client:
        # ---- Login + session ----
        results.append(
            await _check(
                "session.GET /rest-api/session",
                client.fetch_json("rest-api/session"),
                lambda r: f"keys={sorted(r.keys())[:6] if isinstance(r, dict) else type(r).__name__}",
            )
        )

        # ---- Child list ----
        async def _children() -> Any:
            payload = await client.fetch_json("rest-api/parent/header/parent")
            return parse_parent_header(payload, school=settings.school)

        children_result = await _check(
            "list_children (parse_parent_header)",
            _children(),
            lambda r: (
                f"{len(r.children)} children, active_id={r.active_student_id}"
                + (f", note={_redact(r.note)}" if r.note else "")
            ),
        )
        results.append(children_result)

        # ---- Child switch (only if we found at least one child) ----
        # We do it by switching to the FIRST listed child, which is usually
        # already active — making the test idempotent and never disruptive.
        try:
            payload = await client.fetch_json("rest-api/parent/header/parent")
            parsed = parse_parent_header(payload, school=settings.school)
            if parsed.children:
                target = parsed.children[0].student_id
                async def _switch() -> Any:
                    return await client.fetch_json(
                        "rest-api/parent/header/parent",
                        method="PUT",
                        params={"childId": str(target), "orgId": "1"},
                    )
                results.append(
                    await _check(
                        f"set_active_child (PUT childId={target})",
                        _switch(),
                        lambda _: "no error",
                    )
                )
            else:
                results.append(TestResult(
                    "set_active_child", False, "no children parsed; cannot test switch"
                ))
        except Exception as err:
            results.append(TestResult(
                "set_active_child", False, f"setup failed: {err}"
            ))

        # ---- Lunch menu (REST) ----
        results.append(
            await _check(
                f"GET /rest-api/lunchmenu/week/{iso_week}",
                client.fetch_json(f"rest-api/lunchmenu/week/{iso_week}"),
                lambda r: (
                    f"type={type(r).__name__}, "
                    f"len={len(r) if isinstance(r, (list, dict)) else '?'}"
                ),
            )
        )

        # ---- Calendar lessons (REST) ----
        results.append(
            await _check(
                f"GET /rest-api/parent/calendar/lessons/week/{iso_week}",
                client.fetch_json(
                    f"rest-api/parent/calendar/lessons/week/{iso_week}"
                ),
                lambda r: (
                    f"type={type(r).__name__}, "
                    f"len={len(r) if isinstance(r, (list, dict)) else '?'}"
                ),
            )
        )

        # ---- Assignments (REST) ----
        results.append(
            await _check(
                f"GET /rest-api/parent/ps/assignments/start-page?week={iso_week}&year={iso_year}",
                client.fetch_json(
                    "rest-api/parent/ps/assignments/start-page",
                    params={"week": str(iso_week), "year": str(iso_year)},
                ),
                lambda r: f"type={type(r).__name__}",
            )
        )

        # ---- Planning parts (REST) ----
        results.append(
            await _check(
                f"GET /rest-api/parent/ps/planning_parts/start-page?week={iso_week}&year={iso_year}",
                client.fetch_json(
                    "rest-api/parent/ps/planning_parts/start-page",
                    params={"week": str(iso_week), "year": str(iso_year)},
                ),
                lambda r: f"type={type(r).__name__}",
            )
        )

        # ---- News parsing (JSP) ----
        async def _news() -> Any:
            html = await client.fetch_html(NEWS_PATHS[0])
            return parse_news(html, school=settings.school)

        news_result = await _check(
            "get_news (parse_news on right_student_news.jsp)",
            _news(),
            lambda r: (
                f"{len(r.items)} items"
                + (f", first={_redact(r.items[0].title)!r}" if r.items else "")
                + (f", note={_redact(r.note)}" if r.note else "")
            ),
        )
        results.append(news_result)

        # ---- News attachment fetch + text extraction ----
        # We fish out the first news item with attachments and test the
        # two-step download flow.
        try:
            html = await client.fetch_html(NEWS_PATHS[0])
            feed = parse_news(html, school=settings.school)
            target_item = next(
                (i for i in feed.items if i.attachments and i.news_id), None
            )
            if target_item is None:
                results.append(TestResult(
                    "download_attachment + extract_text",
                    False,
                    "no news item with attachments found to test",
                ))
            else:
                fileid = target_item.attachments[0].fileid
                path, params = build_download_path(
                    parent_id=target_item.news_id or 0,
                    type_id=target_item.type_id,
                    fileid=fileid,
                    object_kind="news",
                )

                async def _download() -> Any:
                    return await client.fetch_bytes(path, params=params)

                dl_result = await _check(
                    f"fetch_bytes attachment fileid={fileid}",
                    _download(),
                    lambda r: f"{len(r[0])} bytes, ct={r[1].get('content-type', '?')[:30]}",
                )
                results.append(dl_result)

                if dl_result.ok:
                    content, headers = await client.fetch_bytes(path, params=params)
                    fname = filename_from_headers(headers, f"attachment_{fileid}")
                    ctype = headers.get("content-type", "").split(";")[0].strip()
                    text, truncated, note = extract_text(
                        content, ctype, limit=2000
                    )
                    results.append(TestResult(
                        f"extract_text from {fname!r}",
                        bool(text or note),
                        (
                            f"{len(text)} chars"
                            + (" [truncated]" if truncated else "")
                            + (f", note={_redact(note)}" if note else "")
                        ),
                    ))
        except Exception as err:
            results.append(TestResult(
                "download_attachment", False, f"setup failed: {err}"
            ))
            traceback.print_exc()

    # ---- Summary ----
    print("\nResults")
    print("=" * 60)
    for r in results:
        print(r.line())
    failures = [r for r in results if not r.ok]
    print()
    print(f"{len(results) - len(failures)} / {len(results)} passed")
    return 1 if failures else 0


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
