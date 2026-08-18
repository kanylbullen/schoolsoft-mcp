# AGENTS.md

Instructions for AI coding agents (Claude Code, Cursor, Continue, etc.)
working on `schoolsoft-mcp`. Human contributors are welcome to read this
too — it's a quick map of the project.

## What this project is

An unofficial Model Context Protocol server that scrapes
[SchoolSoft](https://www.schoolsoft.se/) using a parent/student account
and exposes the data as MCP tools. Designed to be embedded in clients
like Claude Desktop, Cursor, and Continue.

This is a **public repository**. Treat every change with that in mind
(see "Security and privacy" below).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest          # 18 tests
ruff check .    # lint
mypy src        # strict type-check
```

CI (`.github/workflows/ci.yml`) runs the same three commands across
Python 3.10–3.13.

For secrets locally, the MCP server reads plain env vars — `.env`, direnv,
OS keychain, the MCP client's `env` block, or any external secrets manager
that can inject vars all work. There is no built-in integration.

To find/verify SchoolSoft JSP paths, use the Playwright-based
discovery script: see [docs/discovery.md](./docs/discovery.md). The
output is gitignored because it contains the operator's school slug.

## Layout

```
src/schoolsoft_mcp/
  __init__.py        # version
  __main__.py        # console entry point (schoolsoft-mcp)
  server.py          # FastMCP server + tool registrations
  client.py          # SchoolSoftClient: httpx session, login, re-auth on 302
  config.py          # Settings.from_env() with validation
  models.py          # pydantic models returned by tools
  parsers/
    lunch.py         # STABLE — ported from a working HA integration
    schedule.py      # EXPERIMENTAL
    homework.py      # EXPERIMENTAL
    attendance.py    # EXPERIMENTAL
    news.py          # EXPERIMENTAL (news + messages)
tests/
  fixtures/          # sanitized HTML snippets (no personal data)
  test_*.py
```

## Tool status

| Tool             | Status        | Confidence | Owner of next fix              |
| ---------------- | ------------- | ---------- | ------------------------------ |
| `get_lunch_menu` | Stable        | High       | Only touch if upstream changes |
| `get_schedule`   | Experimental  | Low        | Needs real-school HTML sample  |
| `get_homework`   | Experimental  | Low        | Needs real-school HTML sample  |
| `get_attendance` | Experimental  | Low        | Needs real-school HTML sample  |
| `get_news`       | Experimental  | Low        | Needs real-school HTML sample  |
| `get_messages`   | Experimental  | Low        | Needs real-school HTML sample  |
| `set_fritids_day_comment` | Experimental | Medium | Needs live parent-account verification |
| `dump_page`      | Debug         | n/a        | Stable contract; keep simple   |

Experimental tools intentionally return a `note` field when they can't
find structured data — that's the signal to file an issue with a
sanitized `dump_page` output.

## How to improve an experimental parser

1. Ask the user (or check the issue) for a sanitized HTML excerpt from
   `dump_page(<relevant path>)`. **Never** ask them to paste an
   un-sanitized dump — names, IDs, and grades must be redacted first.
2. Save the snippet to `tests/fixtures/<page>_sample.html`.
3. Add a test in `tests/test_<page>_parser.py` that loads the fixture
   and asserts on a few stable fields.
4. Update the parser until the test passes. Keep selectors lenient:
   SchoolSoft layouts vary per school, so prefer text-content and
   structural cues over brittle CSS class names.
5. Bump status in the table above when confidence is high enough.

## Conventions

- **Python 3.10+**, `from __future__ import annotations` everywhere, full
  type hints, `mypy --strict` clean.
- **Ruff** with the rules in `pyproject.toml` (E/F/W/I/B/UP/ASYNC/SIM/RUF).
  Run `ruff format .` before committing if you prefer formatted code.
- **Async**: all I/O is async via `httpx.AsyncClient`. Tools serialize
  with `app.lock` so the shared session isn't hammered.
- **No comments explaining what code does**; only add a comment when the
  *why* is non-obvious (a workaround, a SchoolSoft quirk, etc.).
- **Pydantic models** are the return type of every public tool. They
  serialize to JSON automatically via FastMCP.
- **Logging** via `logging.getLogger(__name__)`. Never `print`. Don't log
  credentials, tokens, or full response bodies at INFO level.

## Adding a new tool

1. Add the response shape to `src/schoolsoft_mcp/models.py`.
2. Create `src/schoolsoft_mcp/parsers/<name>.py` with:
   - A tuple `<NAME>_PATHS` of candidate JSP paths (the helper
     `_fetch_first` in `server.py` tries them in order).
   - A `parse_<name>(html, *, school, ...) -> Model` function that
     returns a populated model. When parsing fails gracefully, set
     `note=` on the model rather than raising.
3. Register the tool in `src/schoolsoft_mcp/server.py`:
   ```python
   @mcp.tool()
   async def <name>(ctx: Context[Any, AppContext, Any]) -> Model:
       """One-line description; mark EXPERIMENTAL if applicable."""
       app = _app(ctx)
       async with app.lock:
           html = await _fetch_first(app.client, <NAME>_PATHS)
       return parse_<name>(html, school=app.settings.school)
   ```
4. Add unit tests using a sanitized fixture in `tests/fixtures/`.

## Security and privacy (public-repo rules)

- **Never** commit credentials, cookies, tokens, real school slugs that
  identify a person, or HTML dumps containing names/personal numbers.
  `.env` is in `.gitignore`; don't add new patterns that would leak.
- Example values in docs, comments, and tests must be generic
  (`yourschool`, `alice`, `parent@example.com`).
- Fixtures must be hand-written or thoroughly sanitized. The directory
  `tests/fixtures/private/` is in `.gitignore` if you ever need a local
  scratch space.
- This codebase touches credentials. **Do not** add telemetry, crash
  reporting, or any third-party network call. The only outbound host
  should be the configured SchoolSoft base URL.
- If you spot an insecure pattern (HTML injection in `dump_page` output,
  credential logging, etc.), fix it in the same PR — don't defer.

## Common pitfalls

- **Session expiry**: `SchoolSoftClient.fetch_html` re-logs in once on a
  redirect to the login page. Don't reimplement that flow elsewhere.
- **The selected child is session state**: on a parent account every
  `right_student_*` page and file download resolves against the one child
  currently selected, and a fresh login resets it to SchoolSoft's default.
  `SchoolSoftClient.select_child` remembers the selection and re-applies it
  after each re-auth — go through it (or the `_select_child` helper in
  `server.py`) rather than PUT-ing the header endpoint yourself.
- **`orgId` is not always 1**: it comes from `list_children()[*].org_id`.
  A wrong `orgId` is accepted with a 200 while the session quietly stays on
  the previous child.
- **A 404 on an attachment is not proof the file is gone**: the JSP copies
  it to `/files/<school>/tmp_file_<id>.tmp` and redirects there straight
  away, so a large file can 404 for a second or two while the copy lands
  (seen on a 3.9 MB PDF). `_fetch_attachment` re-requests the JSP with a
  short backoff — never replay the signed URL, it is one-shot.
- **Redirect handling**: the client deliberately sets
  `follow_redirects=False` so login success/failure can be detected from
  the `Location` header.
- **`Self` import**: must come from `typing_extensions` on Python 3.10.
  The current pattern in `client.py` handles both.
- **FastMCP Context generics**: tools use
  `Context[Any, AppContext, Any]`. The middle parameter is the lifespan
  context type; the others are required by FastMCP but unused here.
- **lxml**: parsers depend on `lxml` for speed and lenient HTML. Don't
  switch to `html.parser` without a benchmark.

## What not to do

- Don't add a database, queue, or background worker. This is a stateless
  per-request scraper by design.
- Don't add multi-account support inside one process. Run multiple
  server instances with different env vars instead.
- Don't add a web UI or REST API. MCP over stdio is the only transport.
- Don't bypass `commit.gpgsign` or `--no-verify` unless the user
  explicitly asks for it.
