# Endpoint discovery with Playwright

[`scripts/discover_endpoints.py`](../scripts/discover_endpoints.py) is a
one-shot dev tool that logs into SchoolSoft in a real browser, BFS-crawls
the menu, and records every network request the page makes — including
the XHR/fetch calls that aren't visible in the rendered HTML.

The output (`discovery/endpoints.json`) is the source of truth for which
JSP paths and AJAX endpoints actually exist on a given school's instance.
Use it to:

- Confirm the candidate paths hard-coded in each parser
  (`LUNCH_PATH`, `SCHEDULE_PATHS`, …) still exist.
- Discover new endpoints to wrap as MCP tools.
- See what `content-type` SchoolSoft returns for each path (some are JSON,
  not HTML — those are worth porting from scraping to direct API calls).

## Install

```bash
pip install -e ".[discover]"
playwright install chromium
```

## Two modes

- **`crawl`** (default): logs in and BFS-walks plain `<a href>` links. Good
  for the legacy JSP menu, useless for SchoolSoft's React SPA where every
  navigation is JS.
- **`manual`**: logs in, opens a visible browser, and records every
  network request until you close the window. Use this to capture the
  React app's `/rest-api/*` calls — the script can't click into the SPA
  for you, but it sees every XHR the React UI fires.

Output is merged across runs by default, so the typical flow is:

```bash
# 1. fast automatic pass (bash / zsh)
phase run -- python scripts/discover_endpoints.py

# 2. drive the SPA yourself to capture what BFS can't reach
DISCOVER_MODE=manual phase run -- python scripts/discover_endpoints.py
```

On **PowerShell** (Windows), env vars are set separately:

```powershell
phase run -- python scripts/discover_endpoints.py

$env:DISCOVER_MODE = "manual"
phase run -- python scripts/discover_endpoints.py
Remove-Item Env:DISCOVER_MODE     # so future runs default back to crawl
```

In step 2, click into **every feature you care about**, and especially:

- The child-switcher (each child → records the call that swaps active student).
- Schedule (Schema) — different weeks, both prev/next.
- Lunch menu (Matsedel).
- Attendance (Frånvaro).
- News / Veckobrev — open at least one item that has a `.docx` or `.pdf`
  attachment and **click the attachment** so the file-download URL is recorded.
- Messages (Meddelanden) — open a thread with an attachment if you have one.
- Homework (Läxor) and lesson planning (Planering) — flip between weeks.

Without phase, set the env vars yourself:

```bash
SCHOOLSOFT_SCHOOL=... SCHOOLSOFT_USERNAME=... SCHOOLSOFT_PASSWORD=... \
    python scripts/discover_endpoints.py
```

The script uses the **same env vars as the MCP server** — see
[`.env.example`](../.env.example) — plus a few extras:

| Variable              | Default                       | Notes                                                  |
| --------------------- | ----------------------------- | ------------------------------------------------------ |
| `DISCOVER_MODE`       | `crawl`                       | `crawl` (BFS) or `manual` (drive it yourself).         |
| `DISCOVER_MAX_DEPTH`  | `2`                           | BFS depth — crawl mode only.                           |
| `DISCOVER_MAX_PAGES`  | `60`                          | Hard cap on pages — crawl mode only.                   |
| `DISCOVER_OUTPUT`     | `discovery/endpoints.json`    | Output JSON file.                                      |
| `DISCOVER_HEADLESS`   | `1`                           | Set `0` to watch the browser. Forced off in manual.    |
| `DISCOVER_SAVE_HTML`  | `0`                           | Set `1` to dump HTML to `discovery/pages/` (crawl).    |
| `DISCOVER_OVERWRITE`  | `0`                           | Set `1` to replace the output instead of merging.      |

## Output

```json
{
  "base_url": "https://sms.schoolsoft.se",
  "school": "yourschool",
  "usertype": 2,
  "endpoints": [
    {
      "method": "GET",
      "path": "/yourschool/jsp/student/right_student_lunchmenu.jsp",
      "status": 200,
      "content_type": "text/html",
      "resource_type": "document",
      "size_bytes": 14523,
      "seen_on_pages": ["/yourschool/jsp/student/right_student_startpage.jsp"],
      "sample_query_keys": []
    },
    ...
  ]
}
```

`discovery/` is **gitignored** — the data is per-school and contains your
school slug. Never commit it. If you want to share findings with the
project, redact your slug, drop any personally identifying paths, and
attach the redacted JSON to an issue.

## What it deliberately doesn't do

- It doesn't save response bodies by default — those almost always
  contain personal data (names, grades, messages). Use `DISCOVER_SAVE_HTML=1`
  only locally, and **never** commit the resulting `discovery/pages/`.
- It doesn't recurse forever — depth and page-count caps keep a single
  run bounded, even if SchoolSoft has a calendar with infinite "next
  week" links.
- It skips `Logout.jsp` and obvious static assets to avoid killing its
  own session.
- It is **not** a runtime dependency. The MCP server still talks to
  SchoolSoft over plain `httpx`; Playwright is only here for offline
  reconnaissance.
