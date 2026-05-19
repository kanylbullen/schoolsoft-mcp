"""Capture HTML snapshots of every JSP page in SchoolSoft's sidebar.

Reads the sidebar menu (we already have it as a probed REST sample) and
dumps the rendered HTML for each `right_student_*.jsp` and
`right_parent_*.jsp` entry to ``discovery/samples/pages/<id>.html``.

This is how we get the data needed to write parsers for the JSP pages
that didn't migrate to REST (frånvaro, betyg, kontaktlistor, library, …).

Output is gitignored — pages contain real names, grades, message bodies.

Usage:
    # set SCHOOLSOFT_* env vars first (or source a .env), then:
    python scripts/probe_pages.py

Env vars: same as MCP server, plus:
    PROBE_PAGES_DIR    default discovery/samples/pages
    PROBE_PAGES_FROM   default discovery/samples/sidebar_sectiongroups.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from schoolsoft_mcp.client import (
    SchoolSoftAuthError,
    SchoolSoftClient,
    SchoolSoftConnectionError,
)
from schoolsoft_mcp.config import Settings

logger = logging.getLogger("probe-pages")


def _output_dir() -> Path:
    return Path(os.environ.get("PROBE_PAGES_DIR", "discovery/samples/pages"))


def _sidebar_path() -> Path:
    return Path(
        os.environ.get(
            "PROBE_PAGES_FROM", "discovery/samples/sidebar_sectiongroups.json"
        )
    )


def _menu_items(sidebar: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Yield (item_id, label, jsp_path) for every JSP entry in the sidebar."""
    out: list[tuple[str, str, str]] = []
    for group in sidebar:
        for section in group.get("sections", []):
            for item in section.get("items", []):
                url = item.get("url", "")
                # Skip React routes (they have ../../react/#/...).
                if "/react/" in url or url.startswith("#"):
                    continue
                if not url.endswith(".jsp") and ".jsp?" not in url:
                    continue
                # Resolve relative paths like "right_student_absence.jsp" to
                # the canonical jsp/student/ path. SchoolSoft's sidebar uses
                # bare filenames, expecting the same directory context as
                # the previous page (usually jsp/student/).
                if "/" not in url:
                    url = f"jsp/student/{url}"
                item_id = item.get("id") or item.get("label", "").lower()
                out.append((item_id, item.get("label", ""), url))
    return out


async def _probe_page(
    client: SchoolSoftClient,
    out_dir: Path,
    item_id: str,
    label: str,
    path: str,
) -> bool:
    try:
        html = await client.fetch_html(path.split("?", 1)[0], params=_split_params(path))
    except (SchoolSoftConnectionError, SchoolSoftAuthError) as err:
        logger.warning("[%s] FAILED %s: %s", item_id, path, err)
        (out_dir / f"{item_id}.error.txt").write_text(
            f"GET {path}\n\nERROR: {err}\n", encoding="utf-8"
        )
        return False

    target = out_dir / f"{item_id}.html"
    target.write_text(html, encoding="utf-8")
    logger.info(
        "[%s] OK %s -> %s (%d bytes)", item_id, path, target.name, target.stat().st_size
    )
    return True


def _split_params(path: str) -> dict[str, str] | None:
    if "?" not in path:
        return None
    query = path.split("?", 1)[1]
    out: dict[str, str] = {}
    for pair in query.split("&"):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        out[k] = v
    return out or None


async def _run() -> int:
    sidebar_path = _sidebar_path()
    if not sidebar_path.exists():
        raise SystemExit(
            f"Sidebar JSON not found at {sidebar_path}. "
            "Run scripts/probe_rest.py first."
        )
    sidebar = json.loads(sidebar_path.read_text(encoding="utf-8"))

    items = _menu_items(sidebar)
    if not items:
        logger.warning("No JSP entries found in sidebar — nothing to probe.")
        return 1

    settings = Settings.from_env()
    out_dir = _output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    async with SchoolSoftClient(
        school=settings.school,
        username=settings.username,
        password=settings.password,
        usertype=settings.usertype,
        base_url=settings.base_url,
        timeout=settings.request_timeout,
    ) as client:
        results: list[dict[str, Any]] = []
        for item_id, label, path in items:
            ok = await _probe_page(client, out_dir, item_id, label, path)
            results.append(
                {"id": item_id, "label": label, "path": path, "ok": ok}
            )

    (out_dir / "_summary.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    successes = sum(1 for r in results if r["ok"])
    logger.info(
        "Done — %d/%d pages captured to %s", successes, len(items), out_dir
    )
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
