# schoolsoft-mcp

Unofficial [Model Context Protocol](https://modelcontextprotocol.io) server
for [SchoolSoft](https://www.schoolsoft.se/). Exposes your school's lunch menu,
schedule, homework, attendance, news, and messages as MCP tools so any
MCP-compatible client (Claude Desktop, Cursor, Continue, etc.) can ask
"vad är det till lunch idag?" or "vad har jag för läxor?".

> **Disclaimer:** This project is not affiliated with, endorsed by, or
> sponsored by SchoolSoft AB. It scrapes the publicly accessible web UI
> using your own credentials. Use it at your own discretion and respect
> SchoolSoft's terms of service.

## Status

| Tool              | Status          | Notes                                                         |
| ----------------- | --------------- | ------------------------------------------------------------- |
| `get_lunch_menu`  | Stable          | Ported from a working Home Assistant integration.             |
| `get_schedule`    | Experimental    | Schedules are often JS-rendered; HTML scraping is best-effort.|
| `get_homework`    | Experimental    | Page layouts vary per school.                                 |
| `get_attendance`  | Experimental    | Heuristic date/minute extraction.                             |
| `get_news`        | Experimental    | Parses startpage headings + bodies.                           |
| `get_messages`    | Experimental    | Subject/sender/date are heuristic.                            |
| `dump_page`       | Debug           | Returns raw HTML so parsers can be improved.                  |

Experimental tools return a `note` field when they cannot find structured
data, with guidance on how to help improve the parser.

## Installation

Requires Python 3.10+.

```bash
pip install git+https://github.com/kanylbullen/schoolsoft-mcp.git
```

Or with [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/kanylbullen/schoolsoft-mcp.git
```

This installs a `schoolsoft-mcp` console script that runs the MCP server
over stdio.

## Configuration

The server reads credentials from environment variables:

| Variable                | Required | Default                       | Description                                              |
| ----------------------- | -------- | ----------------------------- | -------------------------------------------------------- |
| `SCHOOLSOFT_SCHOOL`     | yes      | —                             | School slug from the SchoolSoft URL.                     |
| `SCHOOLSOFT_USERNAME`   | yes      | —                             | Your SchoolSoft username.                                |
| `SCHOOLSOFT_PASSWORD`   | yes      | —                             | Your SchoolSoft password.                                |
| `SCHOOLSOFT_USERTYPE`   | no       | `2`                           | `1` = student, `2` = parent, `3` = staff.                |
| `SCHOOLSOFT_BASE_URL`   | no       | `https://sms.schoolsoft.se`   | Override only if your school uses a different host.      |

To find the school slug, look at your normal SchoolSoft login URL:

```
https://sms.schoolsoft.se/<SCHOOL>/jsp/Login.jsp
                          ^^^^^^^^
                          this is SCHOOLSOFT_SCHOOL
```

A `.env.example` is included as a starting point. If you'd rather keep
credentials out of any on-disk file, the server also works behind
[phase.dev](https://phase.dev) — see [docs/phase-dev.md](./docs/phase-dev.md).

## Using with Claude Desktop

Add an entry to `claude_desktop_config.json` (location depends on your OS —
see the [MCP docs](https://modelcontextprotocol.io/quickstart/user)):

```json
{
  "mcpServers": {
    "schoolsoft": {
      "command": "schoolsoft-mcp",
      "env": {
        "SCHOOLSOFT_SCHOOL": "yourschool",
        "SCHOOLSOFT_USERNAME": "your-username",
        "SCHOOLSOFT_PASSWORD": "your-password"
      }
    }
  }
}
```

Restart Claude Desktop and the SchoolSoft tools will appear.

## Using with other MCP clients

Anything that speaks MCP over stdio can run `schoolsoft-mcp` as a child
process and pass the credentials via env vars. Cursor, Continue, and the
`mcp` CLI all work the same way.

## Tools

All tools accept no arguments unless noted.

### Multi-child accounts

- **`list_children()`** — Children attached to this parent account, with
  `student_id`, name, school/grade, and which one is currently active in
  SchoolSoft's session.
- **`set_active_child(student_id: int, org_id?: int = 1)`** — Switch the
  active child. Subsequent data tools return information for this child
  until the session changes again.

### Calendar and assignments

- **`get_lunch_menu(week?: int)`** — Lunch menu for the given ISO week
  (current week by default). One entry per weekday with the main dish
  and the vegetarian alternative (`"main | Veg: alt"`).
- **`get_schedule(week?: int)`** — Lessons for the week (experimental).
- **`get_homework()`** — Current and upcoming assignments (experimental).
- **`get_attendance()`** — Frånvaro/attendance overview (experimental).

### News, veckobrev, and attachments

- **`get_news(older?: bool = False)`** — News and veckobrev. Each item
  carries a `news_id`/`type_id` and a list of `attachments` with their
  `fileid` and `filename`. Set `older=True` for the archived view.
- **`get_news_item(news_id: int, type_id?: int = 1)`** — Fetch one news
  item with the full body and attachments.
- **`download_attachment(news_id: int, fileid: int, type_id?: int = 1, object_kind?: str = "news")`**
  — Download a news or message attachment as base64-encoded bytes. Use
  when you need the raw file (e.g. to save to disk). Otherwise prefer
  `read_attachment_text`, which is far cheaper for LLM context.
- **`read_attachment_text(news_id: int, fileid: int, type_id?: int = 1, object_kind?: str = "news", max_chars?: int = 50_000)`**
  — Download an attachment and return its extracted plain text. Supports
  PDF (via `pypdf`), `.docx` (via `python-docx`), and plain-text files.
  The right tool for *"vad står det i veckobrevet?"*.

### Messages

- **`get_messages()`** — Inbox messages (experimental).

### Debugging

- **`dump_page(path: str, max_bytes?: int)`** — Fetch the raw HTML of any
  SchoolSoft path. Strip personal information before sharing.
- **`dump_json(path: str, method?: str = "GET")`** — Fetch a `rest-api/*`
  endpoint and return the parsed JSON. Companion to `dump_page` for the
  REST surface.

## Security

- Your SchoolSoft credentials are sent only to `sms.schoolsoft.se` (or the
  base URL you configured), exactly as the browser would.
- Credentials live in environment variables or the MCP client's config
  file. Never commit them. The included `.gitignore` excludes `.env`.
- The MCP server runs as a local subprocess — no data is sent to any
  third-party server beyond SchoolSoft itself.
- Tool responses may contain personal information (names, grades,
  messages). Treat them with the same care as your SchoolSoft account.

## Development

```bash
git clone https://github.com/kanylbullen/schoolsoft-mcp.git
cd schoolsoft-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

pytest          # run tests
ruff check .    # lint
mypy src        # type-check
```

### Discovering endpoints with Playwright

If you don't know which JSP path a feature lives behind, run the
discovery script to log in and crawl your school's UI in a real browser
while recording every network request:

```bash
pip install -e ".[discover]"
playwright install chromium
phase run -- python scripts/discover_endpoints.py
# or set the SCHOOLSOFT_* env vars yourself and drop `phase run --`
```

See [docs/discovery.md](./docs/discovery.md) for output format and knobs.
The resulting `discovery/endpoints.json` is gitignored.

### Improving experimental parsers

If a tool returns empty results with a `note`, the easiest way to help is:

1. Call `dump_page` with the relevant path (e.g.
   `jsp/student/right_student_homework.jsp`).
2. **Remove any personal data** from the HTML (names, IDs, dates).
3. Open an issue with the sanitised snippet so the parser can be updated.

PRs adding fixtures + parser improvements are very welcome.

## License

MIT — see [`LICENSE`](./LICENSE).
