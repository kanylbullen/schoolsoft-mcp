"""Playwright-based endpoint discovery for SchoolSoft.

Logs in with the same env vars the MCP server uses, then BFS-crawls
same-host links from the start page (bounded depth) while recording every
network request the browser makes — including XHR/fetch calls that aren't
visible in the rendered HTML.

This is a one-shot dev tool, not part of the runtime MCP. Output is
endpoint *metadata only* (URL, method, status, content-type, size) — no
response bodies, no DOM dumps. Personal data should never leak into the
output, but the school slug will, so the resulting JSON is gitignored.

Usage:
    pip install -e ".[discover]"
    playwright install chromium
    phase run -- python scripts/discover_endpoints.py
    # or, without phase:
    python scripts/discover_endpoints.py

Env vars (same as the MCP server):
    SCHOOLSOFT_SCHOOL, SCHOOLSOFT_USERNAME, SCHOOLSOFT_PASSWORD,
    SCHOOLSOFT_USERTYPE (default 2), SCHOOLSOFT_BASE_URL.

Extra knobs:
    DISCOVER_MODE           "crawl" (default) BFS-crawls <a href> links.
                            "manual" logs in, opens a visible browser, and
                            records traffic until you close the window or
                            press Ctrl+C. Use this for the React SPA where
                            navigation happens via JS, not real links.
    DISCOVER_MAX_DEPTH      BFS depth from the start page (default 2).
                            Ignored in manual mode.
    DISCOVER_MAX_PAGES      hard cap on pages visited (default 60).
                            Ignored in manual mode.
    DISCOVER_OUTPUT         output JSON path (default discovery/endpoints.json).
    DISCOVER_HEADLESS       "0" to watch the browser (default headless).
                            Forced to "0" in manual mode.
    DISCOVER_SAVE_HTML      "1" to also dump sanitized-by-you HTML per page
                            into discovery/pages/ (gitignored). Off by default.
                            Only effective in crawl mode.
    DISCOVER_OVERWRITE      "1" to replace the output file. Default merges
                            with whatever's already there so successive runs
                            (crawl, then manual) accumulate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urldefrag, urlparse

try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Request,
        Response,
        async_playwright,
    )
except ImportError as err:  # pragma: no cover - dev-only script
    raise SystemExit(
        "Playwright is not installed. Run:\n"
        '    pip install -e ".[discover]"\n'
        "    playwright install chromium"
    ) from err

logger = logging.getLogger("discover")

# Anything matching these is skipped — logout would kill the session,
# external trackers are noise, and pure static assets aren't endpoints
# we'd ever scrape from Python.
SKIP_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/Logout\.jsp", re.IGNORECASE),
    re.compile(r"action=logout", re.IGNORECASE),
    re.compile(r"\.(?:png|jpe?g|gif|svg|ico|woff2?|ttf|css|map)(?:\?|$)", re.IGNORECASE),
)

# Resource types from Playwright that we don't care about as endpoints.
SKIP_RESOURCE_TYPES = {"image", "font", "stylesheet", "media"}


@dataclass(frozen=True, slots=True)
class EndpointKey:
    method: str
    path: str  # path + query, no scheme/host

    def as_tuple(self) -> tuple[str, str]:
        return (self.method, self.path)


@dataclass(slots=True)
class EndpointRecord:
    method: str
    path: str
    status: int | None = None
    content_type: str | None = None
    resource_type: str | None = None
    size_bytes: int | None = None
    seen_on_pages: set[str] = field(default_factory=set)
    sample_query_keys: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "path": self.path,
            "status": self.status,
            "content_type": self.content_type,
            "resource_type": self.resource_type,
            "size_bytes": self.size_bytes,
            "seen_on_pages": sorted(self.seen_on_pages),
            "sample_query_keys": sorted(self.sample_query_keys),
        }


@dataclass(slots=True)
class DiscoveryConfig:
    school: str
    username: str
    password: str
    usertype: int
    base_url: str
    mode: str
    max_depth: int
    max_pages: int
    output_path: Path
    headless: bool
    save_html: bool
    overwrite: bool

    @classmethod
    def from_env(cls) -> DiscoveryConfig:
        def required(name: str, *, strip: bool = True) -> str:
            value = os.environ.get(name, "")
            if strip:
                value = value.strip()
            if not value:
                raise SystemExit(f"Missing required env var: {name}")
            return value

        mode = os.environ.get("DISCOVER_MODE", "crawl").strip().lower()
        if mode not in {"crawl", "manual"}:
            raise SystemExit(
                f"DISCOVER_MODE must be 'crawl' or 'manual', got {mode!r}"
            )
        # Manual mode requires a visible window, otherwise the user can't drive.
        headless = os.environ.get("DISCOVER_HEADLESS", "1") != "0"
        if mode == "manual":
            headless = False

        return cls(
            school=required("SCHOOLSOFT_SCHOOL"),
            username=required("SCHOOLSOFT_USERNAME"),
            # strip=False — leading/trailing whitespace in passwords is valid.
            password=required("SCHOOLSOFT_PASSWORD", strip=False),
            usertype=int(os.environ.get("SCHOOLSOFT_USERTYPE", "2")),
            base_url=os.environ.get(
                "SCHOOLSOFT_BASE_URL", "https://sms.schoolsoft.se"
            ).rstrip("/"),
            mode=mode,
            max_depth=int(os.environ.get("DISCOVER_MAX_DEPTH", "2")),
            max_pages=int(os.environ.get("DISCOVER_MAX_PAGES", "60")),
            output_path=Path(
                os.environ.get("DISCOVER_OUTPUT", "discovery/endpoints.json")
            ),
            headless=headless,
            save_html=os.environ.get("DISCOVER_SAVE_HTML", "0") == "1",
            overwrite=os.environ.get("DISCOVER_OVERWRITE", "0") == "1",
        )


def _should_skip(url: str) -> bool:
    return any(p.search(url) for p in SKIP_URL_PATTERNS)


def _same_host(url: str, base_host: str) -> bool:
    try:
        return urlparse(url).netloc == base_host
    except ValueError:
        return False


def _path_with_query(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _query_keys(url: str) -> set[str]:
    parsed = urlparse(url)
    if not parsed.query:
        return set()
    return {pair.split("=", 1)[0] for pair in parsed.query.split("&") if pair}


class Recorder:
    """Aggregates request/response pairs into one EndpointRecord per (method, path)."""

    def __init__(self, base_host: str) -> None:
        self._base_host = base_host
        self._records: dict[EndpointKey, EndpointRecord] = {}

    def attach(self, page: Page) -> None:
        page.on("request", lambda req: self._on_request(req, page))
        page.on("response", lambda resp: self._on_response(resp, page))

    def _on_request(self, request: Request, page: Page) -> None:
        url = request.url
        if not _same_host(url, self._base_host):
            return
        if request.resource_type in SKIP_RESOURCE_TYPES:
            return
        if _should_skip(url):
            return

        key = EndpointKey(method=request.method.upper(), path=_path_with_query(url))
        record = self._records.get(key)
        if record is None:
            record = EndpointRecord(
                method=key.method,
                path=key.path,
                resource_type=request.resource_type,
            )
            self._records[key] = record
        record.seen_on_pages.add(_path_with_query(page.url))
        record.sample_query_keys.update(_query_keys(url))

    def _on_response(self, response: Response, _page: Page) -> None:
        url = response.url
        if not _same_host(url, self._base_host):
            return
        method = response.request.method.upper()
        key = EndpointKey(method=method, path=_path_with_query(url))
        record = self._records.get(key)
        if record is None:
            return
        record.status = response.status
        record.content_type = response.headers.get("content-type", "").split(";")[0].strip() or None
        size_header = response.headers.get("content-length")
        if size_header and size_header.isdigit():
            record.size_bytes = int(size_header)

    def snapshot(self) -> list[EndpointRecord]:
        return sorted(self._records.values(), key=lambda r: (r.path, r.method))


async def _login(page: Page, config: DiscoveryConfig) -> None:
    """Submit the JSP login form. Mirrors SchoolSoftClient.login()."""
    login_url = f"{config.base_url}/{config.school}/jsp/Login.jsp"
    logger.info("Navigating to %s", login_url)
    await page.goto(login_url, wait_until="domcontentloaded")

    # The JSP form fields are ssusername / sspassword. We submit the form
    # directly rather than relying on label heuristics, since SchoolSoft
    # markup varies by school.
    await page.evaluate(
        """({ username, password, usertype }) => {
            const form = document.querySelector('form[action*="Login"], form');
            if (!form) throw new Error('No login form found on page');
            const set = (name, value) => {
                let el = form.querySelector(`[name="${name}"]`);
                if (!el) {
                    el = document.createElement('input');
                    el.type = 'hidden';
                    el.name = name;
                    form.appendChild(el);
                }
                el.value = value;
            };
            set('ssusername', username);
            set('sspassword', password);
            set('usertype', String(usertype));
            set('action', 'login');
            form.submit();
        }""",
        {
            "username": config.username,
            "password": config.password,
            "usertype": config.usertype,
        },
    )
    await page.wait_for_load_state("networkidle")

    if "Login.jsp" in page.url and "loginfailed" not in page.url.lower():
        # Some schools land back on Login.jsp briefly; give it one more tick.
        await page.wait_for_load_state("networkidle")
    if "loginfailed" in page.url.lower() or "Login.jsp" in page.url:
        raise SystemExit(
            f"Login appears to have failed. Browser landed on: {page.url}\n"
            "Verify SCHOOLSOFT_SCHOOL, SCHOOLSOFT_USERNAME, SCHOOLSOFT_PASSWORD, SCHOOLSOFT_USERTYPE."
        )
    logger.info("Logged in, post-login URL: %s", page.url)


async def _collect_links(page: Page) -> list[str]:
    """Extract same-document hrefs from the current page."""
    raw = await page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => e.href).filter(h => h && !h.startsWith('javascript:'))",
    )
    seen: set[str] = set()
    out: list[str] = []
    for href in raw:
        defragged, _ = urldefrag(href)
        if defragged in seen:
            continue
        seen.add(defragged)
        out.append(defragged)
    return out


def _is_school_url(url: str, base_url: str, school: str) -> bool:
    if not url.startswith(base_url):
        return False
    return urlparse(url).path.lstrip("/").startswith(f"{school}/")


async def _save_html(page: Page, output_dir: Path) -> None:
    """Best-effort dump of the visited page's HTML for later parser work.

    Files go under discovery/pages/ (gitignored). The user is expected to
    sanitize before sharing.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path_part = urlparse(page.url).path.strip("/").replace("/", "_") or "root"
    query = urlparse(page.url).query
    suffix = f"__{query.replace('=', '-').replace('&', '_')}" if query else ""
    target = output_dir / f"{path_part}{suffix}.html"
    try:
        target.write_text(await page.content(), encoding="utf-8")
    except OSError as err:
        logger.warning("Could not save HTML for %s: %s", page.url, err)


async def _crawl(
    context: BrowserContext,
    start_url: str,
    config: DiscoveryConfig,
    recorder: Recorder,
) -> None:
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])

    page = await context.new_page()
    recorder.attach(page)
    pages_dir = config.output_path.parent / "pages"

    while queue and len(visited) < config.max_pages:
        url, depth = queue.popleft()
        defragged, _ = urldefrag(url)
        if defragged in visited:
            continue
        if not _is_school_url(defragged, config.base_url, config.school):
            continue
        if _should_skip(defragged):
            continue
        visited.add(defragged)

        logger.info("[%2d/%d  d=%d] %s", len(visited), config.max_pages, depth, defragged)
        try:
            await page.goto(defragged, wait_until="networkidle", timeout=20_000)
        except Exception as err:
            logger.warning("nav failed: %s (%s)", defragged, err)
            continue

        if "Login.jsp" in page.url:
            logger.warning("Bounced to login on %s — session expired, stopping.", defragged)
            break

        if config.save_html:
            await _save_html(page, pages_dir)

        if depth >= config.max_depth:
            continue
        for link in await _collect_links(page):
            if link not in visited:
                queue.append((link, depth + 1))


async def _record_manual(
    browser: Browser, context: BrowserContext, recorder: Recorder
) -> None:
    """Keep recording until the user closes the browser window or presses Ctrl+C.

    Playwright's ``browser.on("disconnected")`` and ``context.on("close")``
    events are both best-effort and don't fire reliably when the user closes
    the window manually. Instead we poll ``browser.is_connected()`` every
    second — slightly less elegant but actually works.
    """
    stop = asyncio.Event()

    def _on_new_page(page: Page) -> None:
        recorder.attach(page)
        logger.info("New page opened: %s", page.url or "(blank)")

    context.on("page", _on_new_page)
    # Best-effort event listeners (cheap, harmless if they fire early).
    browser.on("disconnected", lambda _b: stop.set())

    for page in context.pages:
        recorder.attach(page)

    logger.info(
        "Manual mode: drive the browser through every feature you want to "
        "see (byt barn, schema, matsedel, frånvaro, nyheter, meddelanden). "
        "Close the browser window — or press Ctrl+C — when done."
    )

    last_logged_total = 0
    last_heartbeat = asyncio.get_running_loop().time()
    try:
        while not stop.is_set():
            await asyncio.sleep(1)
            if not browser.is_connected():
                logger.info("Browser closed — finalising.")
                stop.set()
                break
            now = asyncio.get_running_loop().time()
            total = len(recorder.snapshot())
            # Log when something changed, or once a minute as a liveness signal.
            if total != last_logged_total or (now - last_heartbeat) >= 60:
                logger.info("  ... recorded %d unique endpoints so far", total)
                last_logged_total = total
                last_heartbeat = now
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Interrupted — saving what we've got.")
        stop.set()


async def _run(config: DiscoveryConfig) -> int:
    base_host = urlparse(config.base_url).netloc
    recorder = Recorder(base_host)

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=config.headless)
        context = await browser.new_context(
            user_agent=(
                "schoolsoft-mcp-discover/0.1 "
                "(+https://github.com/kanylbullen/schoolsoft-mcp)"
            ),
            ignore_https_errors=False,
        )
        try:
            login_page = await context.new_page()
            recorder.attach(login_page)
            await _login(login_page, config)
            start_url = login_page.url

            if config.mode == "manual":
                await _record_manual(browser, context, recorder)
            else:
                await login_page.close()
                await _crawl(context, start_url, config, recorder)
        finally:
            for closer in (context.close, browser.close):
                try:
                    await closer()
                except Exception as err:
                    logger.debug("Cleanup ignored: %s", err)

    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    new_records = [r.to_dict() for r in recorder.snapshot()]

    if not config.overwrite and config.output_path.exists():
        merged = _merge_with_existing(config.output_path, new_records)
        endpoints = merged
        merge_note = " (merged with existing)"
    else:
        endpoints = new_records
        merge_note = ""

    payload = {
        "base_url": config.base_url,
        "school": config.school,
        "usertype": config.usertype,
        "endpoints": endpoints,
    }
    config.output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "Wrote %d endpoints to %s%s",
        len(payload["endpoints"]),
        config.output_path,
        merge_note,
    )
    return 0


def _merge_with_existing(
    path: Path, new_records: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Union new records into existing ones by (method, path).

    Status/content-type/size from the new run win when present; pages and
    query-keys are unioned. Returns the combined sorted list.
    """
    try:
        existing = json.loads(path.read_text(encoding="utf-8")).get("endpoints", [])
    except (OSError, json.JSONDecodeError):
        return new_records

    def key(r: dict[str, object]) -> tuple[str, str]:
        return (str(r.get("method", "")), str(r.get("path", "")))

    merged: dict[tuple[str, str], dict[str, object]] = {key(r): dict(r) for r in existing}
    for new in new_records:
        k = key(new)
        if k not in merged:
            merged[k] = dict(new)
            continue
        cur = merged[k]
        for field_name in ("status", "content_type", "size_bytes", "resource_type"):
            new_val = new.get(field_name)
            if new_val is not None:
                cur[field_name] = new_val
        cur_pages = set(cur.get("seen_on_pages") or [])
        cur_pages.update(new.get("seen_on_pages") or [])
        cur["seen_on_pages"] = sorted(cur_pages)
        cur_qk = set(cur.get("sample_query_keys") or [])
        cur_qk.update(new.get("sample_query_keys") or [])
        cur["sample_query_keys"] = sorted(cur_qk)
    return sorted(merged.values(), key=lambda r: (str(r.get("path")), str(r.get("method"))))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = DiscoveryConfig.from_env()
    return asyncio.run(_run(config))


if __name__ == "__main__":
    sys.exit(main())
