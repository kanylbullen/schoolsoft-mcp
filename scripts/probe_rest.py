"""Capture sample JSON responses from SchoolSoft's REST API.

Logs in with the same env vars the MCP server uses, then hits the
endpoints we discovered with Playwright and writes the responses to
``discovery/samples/<endpoint>.json``. Used to:

- See the actual JSON shape so parsers can be written/refined.
- Catch regressions when SchoolSoft changes a response shape.

The output directory is gitignored (responses include real names,
grades, message bodies, etc.).

Usage:
    # set SCHOOLSOFT_* env vars first (or source a .env), then:
    python scripts/probe_rest.py

Env vars: same as the MCP server (SCHOOLSOFT_SCHOOL etc.) plus:
    PROBE_OUTPUT_DIR   default discovery/samples
    PROBE_WEEK         ISO week to query (default: current).
    PROBE_YEAR         year (default: current).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from schoolsoft_mcp.client import SchoolSoftClient
from schoolsoft_mcp.config import Settings

logger = logging.getLogger("probe")


def _output_dir() -> Path:
    return Path(os.environ.get("PROBE_OUTPUT_DIR", "discovery/samples"))


def _iso_week() -> tuple[int, int]:
    today = dt.date.today()
    iso = today.isocalendar()
    return iso[0], iso[1]  # (year, week)


def _build_targets() -> list[tuple[str, str, dict[str, str] | None, str]]:
    """Returns a list of (label, path, params, method) tuples to probe."""
    year_env = os.environ.get("PROBE_YEAR")
    week_env = os.environ.get("PROBE_WEEK")
    iso_year, iso_week = _iso_week()
    year = int(year_env) if year_env else iso_year
    week = int(week_env) if week_env else iso_week

    return [
        ("session", "rest-api/session", None, "GET"),
        ("parameters", "rest-api/parameters", None, "GET"),
        ("properties", "rest-api/properties", None, "GET"),
        ("header_parent", "rest-api/parent/header/parent", None, "GET"),
        (
            "header_messages_amount",
            "rest-api/parent/header/parent/messages/amount",
            None,
            "GET",
        ),
        ("sidebar_sectiongroups", "rest-api/parent/sidebar/sectiongroups", None, "GET"),
        ("logo", "rest-api/parent/logo/", None, "GET"),
        (
            "lunchmenu_current",
            f"rest-api/lunchmenu/week/{week}",
            None,
            "GET",
        ),
        (
            "calendar_lessons_week",
            f"rest-api/parent/calendar/lessons/week/{week}",
            None,
            "GET",
        ),
        (
            "calendar_events_week",
            f"rest-api/parent/calendar/event/year/{year}/week/{week}",
            None,
            "GET",
        ),
        ("calendar_settings", "rest-api/parent/calendar/settings", None, "GET"),
        (
            "calendar_timebookings",
            "rest-api/parent/calendar/timebookings",
            None,
            "GET",
        ),
        (
            "assignments",
            "rest-api/parent/ps/assignments/start-page",
            {"week": str(week), "year": str(year)},
            "GET",
        ),
        (
            "planning_parts",
            "rest-api/parent/ps/planning_parts/start-page",
            {"week": str(week), "year": str(year)},
            "GET",
        ),
        ("ical_settings", "rest-api/parent/ical/settings", None, "GET"),
    ]


async def _probe_one(
    client: SchoolSoftClient,
    out_dir: Path,
    label: str,
    path: str,
    params: dict[str, str] | None,
    method: str,
) -> bool:
    try:
        payload = await client.fetch_json(path, params=params, method=method)
    except Exception as err:
        logger.warning("[%s] FAILED %s: %s", label, path, err)
        (out_dir / f"{label}.error.txt").write_text(
            f"{method} {path}\nparams={params}\n\nERROR: {err}\n",
            encoding="utf-8",
        )
        return False

    target = out_dir / f"{label}.json"
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    size = target.stat().st_size
    logger.info("[%s] OK %s -> %s (%d bytes)", label, path, target, size)
    return True


async def _run() -> int:
    settings = Settings.from_env()
    out_dir = _output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = _build_targets()
    summary: dict[str, Any] = {
        "school": settings.school,
        "usertype": settings.usertype,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "targets": [],
    }

    async with SchoolSoftClient(
        school=settings.school,
        username=settings.username,
        password=settings.password,
        usertype=settings.usertype,
        base_url=settings.base_url,
        timeout=settings.request_timeout,
    ) as client:
        for label, path, params, method in targets:
            ok = await _probe_one(client, out_dir, label, path, params, method)
            summary["targets"].append(
                {
                    "label": label,
                    "method": method,
                    "path": path,
                    "params": params,
                    "ok": ok,
                }
            )

    (out_dir / "_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    successes = sum(1 for t in summary["targets"] if t["ok"])
    logger.info("Done — %d/%d endpoints captured to %s", successes, len(targets), out_dir)
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
