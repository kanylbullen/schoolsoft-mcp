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

| Tool                    | Status       | Notes                                                            |
| ----------------------- | ------------ | ---------------------------------------------------------------- |
| `get_lunch_menu`        | Stable       | Ported from a working Home Assistant integration.                |
| `get_schedule`          | Stable       | REST calendar rows; the JSP scraper remains as a fallback.       |
| `get_homework`          | Stable       | REST assignment grid plus each assignment's full text.           |
| `get_planning`          | Stable       | REST subject-room grid plus each planning's body.                |
| `get_planning_detail`   | Stable       | One planning in full, with its files and links.                  |
| `get_subject_rooms`     | Stable       | Subject rooms and teachers; `activity_id` is the join key.       |
| `get_exam_schedule`     | Stable       | Dates are the announcement window, not the exam date.            |
| `get_lesson_detail`     | Stable       | Room, teachers and group for one lesson.                         |
| `get_day_briefing`      | Stable       | Joins a day's schedule to the plannings that apply to it.        |
| `get_assessments`       | Stable       | Sammantagen bedömning, including the at-risk flag.               |
| `get_assessment_detail` | Stable       | Teacher's text, graded work, warning motivation.                 |
| `get_results`           | Stable       | Says a result was published; the grade is on the assessment.     |
| `get_open_work`         | Stable       | What is outstanding, independent of week.                        |
| `get_unreported_absence`| Stable       | Splits absences awaiting a guardian from ones already confirmed. |
| `get_fritids_times`     | Stable       | After-school care: drop-off and pick-up per day, school hours beside them. |
| `get_student_documents` | Stable       | IUP documents and who has filled in each part.                    |
| `get_student_document`  | Stable       | One IUP part in full: goals, method, wellbeing, attendees.        |
| `get_grades`            | Stable       | Betyg. Near-empty for years that use assessments instead.        |
| `get_attendance`        | Experimental | Heuristic date/minute extraction.                                |
| `get_news`              | Experimental | Parses startpage headings + bodies.                              |
| `get_messages`          | Experimental | Subject/sender/date are heuristic.                               |
| `dump_page`             | Debug        | Returns raw HTML so parsers can be improved.                     |

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

A `.env.example` is included as a starting point. The recommended way to
pass credentials to MCP clients is the client's own `env` block (see
"Using with Claude Desktop" below) — this avoids putting a `.env` file on
disk. Any external secrets manager works too, as long as it can inject
the variables into the spawned MCP server process.

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

### Windows notes

`schoolsoft-mcp` is installed by pip into your Python install's `Scripts/`
directory — typically:

```
%LOCALAPPDATA%\Programs\Python\Python311\Scripts\schoolsoft-mcp.exe
```

Claude Desktop's spawned subprocess sometimes can't resolve `schoolsoft-mcp`
through `PATH` even when it's there. If you see `'schoolsoft-mcp' is not
recognized` or `ModuleNotFoundError: No module named 'schoolsoft_mcp'` in
`%APPDATA%\Claude\logs\mcp-server-schoolsoft.log`, point at the absolute
path instead:

```json
{
  "mcpServers": {
    "schoolsoft": {
      "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python311\\Scripts\\schoolsoft-mcp.exe",
      "env": { "SCHOOLSOFT_SCHOOL": "...", "SCHOOLSOFT_USERNAME": "...", "SCHOOLSOFT_PASSWORD": "..." }
    }
  }
}
```

Double backslashes are required in JSON. Find your exact path with
`(Get-Command schoolsoft-mcp).Source` in PowerShell after install.

If Claude Desktop fails to start the server, kill any orphaned
`schoolsoft-mcp.exe` processes (Task Manager → Details, or
`Get-Process schoolsoft-mcp | Stop-Process`) before re-running `pip install`.

## Using with Claude Code

[Claude Code](https://claude.com/claude-code) (CLI / VS Code extension) also
speaks MCP. From the project root or anywhere on your system:

```bash
claude mcp add schoolsoft \
  --env SCHOOLSOFT_SCHOOL=yourschool \
  --env SCHOOLSOFT_USERNAME=your-username \
  --env SCHOOLSOFT_PASSWORD=your-password \
  -- schoolsoft-mcp
```

On **Windows / PowerShell**, line continuations use a backtick:

```powershell
claude mcp add schoolsoft `
  --env SCHOOLSOFT_SCHOOL=yourschool `
  --env SCHOOLSOFT_USERNAME=your-username `
  --env SCHOOLSOFT_PASSWORD=your-password `
  -- schoolsoft-mcp
```

Or as a one-liner (no line continuations):

```powershell
claude mcp add schoolsoft --env SCHOOLSOFT_SCHOOL=yourschool --env SCHOOLSOFT_USERNAME=your-username --env SCHOOLSOFT_PASSWORD=your-password -- schoolsoft-mcp
```

Same absolute-path caveat as Claude Desktop applies if `schoolsoft-mcp` isn't
on `PATH` in the spawned shell — pass the full `.exe` path instead of the
bare command name.

After adding, verify with `claude mcp list`. The tools become available in
your next Claude Code session.

## Using with other MCP clients

Anything that speaks MCP over stdio can run `schoolsoft-mcp` as a child
process and pass the credentials via env vars. Cursor, Continue, and the
`mcp` CLI all work the same way.

## Tools

All tools accept no arguments unless noted.

### Multi-child accounts

> **Every data tool takes `student_id`.** On a parent account the SchoolSoft
> session carries exactly one selected child, and a tool called without
> `student_id` answers for whichever child happens to be selected — with a
> 200, not an error. Passing it explicitly is the only way a response cannot
> silently be a sibling's.

- **`list_children()`** — Children attached to this parent account, with
  `student_id`, `org_id`, name, school/grade, and which one is currently
  active in SchoolSoft's session.
- **`set_active_child(student_id: int, org_id?: int)`** — Switch the
  active child. Subsequent data tools return information for this child,
  and the selection is re-applied automatically if the session expires
  mid-run. `org_id` is looked up from `list_children` when omitted; it is
  school-specific, and SchoolSoft accepts a wrong one without complaint
  while leaving the previous child selected.

> **The session has exactly one selected child.** Every `right_student_*`
> page *and every attachment download* resolves against it. Asking for one
> child's veckobrev while another is selected returns a 404 from
> SchoolSoft's file server, not an error page — so the news, message, and
> attachment tools take a `student_id` and switch first.

### Calendar and assignments

- **`get_lunch_menu(week?: int, student_id?: int)`** — Lunch menu for the given ISO week
  (current week by default). One entry per weekday with the main dish
  and the vegetarian alternative (`"main | Veg: alt"`).
- **`get_schedule(week?: int, year?: int, student_id?: int)`** — Lessons
  for the given ISO week (defaults to current). Each lesson carries
  start/end times, subject, teacher, room, teaching group, `lesson_id`,
  hex `color`, `attendance_status` (if reported), and `is_break` for
  breaks/lunch. All-day events (sport days, planning bands) are merged
  into `all_day_events`. Uses REST when available, falls back to the
  legacy JSP scraper on non-2xx.
- **`get_homework(week?, year?, student_id?, include_body? = True, max_body_chars? = 4000)`**
  — Assignments / läxor / prov overlapping the given ISO week. Each item
  carries `title`, `subject`, `kind` (e.g. "Diagnos", "Läxa"), ISO
  `start_date`/`end_date`, `teacher`, `status`, `submission_status`,
  `result_status`, IDs, and `body` — the assignment's full text, which
  the week-scoped start-page endpoint does not return.
- **`get_planning(week?, year?, student_id?, include_body? = True, max_body_chars? = 4000)`**
  — Lesson plans (planeringar) **in force during** the given ISO week.

  Selection is by date overlap, not by week bucket: a term-long planning
  ("Idrott och hälsa terminen") runs from August to December and is dropped
  entirely by a `?week=N` query — and it is exactly that kind of planning
  that carries the week-by-week detail.

  Each `PlanningPart` carries subject, teacher, ISO `start_date`/`end_date`,
  and two text fields:

  - `body` — the teacher's own planning text, HTML flattened to plain text.
  - `week_lines` — the line(s) of `body` naming the requested week, e.g.
    `"v.37 Orientering (samling vid klubbstugan)"`. A term-long planning is
    in force every school day but only one of its lines is about any given
    week; this is that line. Week lines are read from the **untruncated**
    body, so a December row still comes out under a small `max_body_chars`.
  - `mentions_weeks` — whether the body is organised by week at all. Empty
    `week_lines` with `mentions_weeks: true` means the teacher wrote about
    other weeks but not this one, which is not the same as a plan with no
    week structure. Do not read `body` as if it described the week you
    asked for.

  A planning written as a `Vecka | Innehåll` table works the same way: the
  header is carried onto each row, so a row reading `34-36` answers a
  question about week 35.
- **`get_planning_detail(part_id: int, week?: int, student_id?: int, max_body_chars? = 20000)`**
  — One planning in full, with the files and links the teacher attached.
- **`get_subject_rooms(student_id?: int, include_teachers? = True)`** —
  The child's subject rooms (ämnesrum) with their teachers. Each room's
  `activity_id` is the join key used across plannings, assignments and
  lessons.
- **`get_exam_schedule(student_id?: int)`** — Announced exams. Note that
  `start`/`end` are the window the announcement is *shown* in, not when
  the exam is written; the exam's own date is on the matching assignment
  in `get_homework`.
- **`get_lesson_detail(lesson_id: int, student_id?: int)`** — Room,
  teachers and group for one scheduled lesson.

### One call for "what does this child need today"

- **`get_day_briefing(date?: str, student_id?: int, news_days? = 14, max_body_chars? = 2000)`**
  — The day's lessons **with the planning text that applies to that week
  attached to each lesson**, plus assignments due today and this week,
  announced exams, unreported absence, and recent news/veckobrev.

  Prefer this over calling `get_schedule` + `get_planning` + `get_homework`
  separately. The joining is the point: a schedule row says "Idrott 08:20",
  and only the subject's planning says the class is meeting at the
  disc-golf course rather than at school. Read apart, that detail goes
  missing while the summary still looks correct.

  `prepare` is the short list to act on before leaving: lessons needing kit
  or a different meeting point, and work due today or tomorrow. Every entry
  is derived from fetched data — a preparation-heavy lesson with no
  published planning says so rather than guessing what to bring, and a
  planning that is silent about this particular week says *that* rather
  than offering some other week's meeting point. Announced exams are not in
  `prepare`; their dates are the announcement window, not the exam date.

  Bodies are fetched only for the subjects on that day's timetable, so
  `plannings` holds the day's plannings rather than the whole term's. Use
  `get_planning` for the full listing.

  A section that fails to load is named in `errors` with its message, and a
  day that looks empty because the schedule fetch failed says so instead of
  reporting a holiday.

### Assessment, results and outstanding work

- **`get_assessments(student_id?: int)`** — Sammantagen bedömning, one row
  per subject, flagged subjects first.

  This is what a Swedish school publishes for the years that carry no formal
  grades. On those years `get_grades` (Betyg) is close to empty while this
  holds everything the teachers have said, so prefer it below the grading
  years. Each subject carries the school's own wording ("Godtagbara
  kunskaper", "Mer än godtagbara kunskaper"), when it was updated and
  published, and whether a guardian has read it.

  `subject_warning` is the school's flag that a subject risks not reaching
  the goals. Flagged subjects sort first and repeat in `warnings`. An empty
  `warnings` is an answer worth stating, not an absence worth omitting.

  Subjects the school has not assessed yet are returned with
  `published: false` and are not counted as unread — a guardian has not
  failed to read something that was never written.
- **`get_assessment_detail(assessment_id: int, student_id?: int)`** — One
  subject in full: the knowledge-development wording, any support measures,
  the teacher's formative comments, the graded work behind it, the warning
  with its motivation, and the earlier terms the subject was assessed in.

  `assessed_work[].grade` is where an actual grade like `"B"` lives.

  `published_sections` says which sections the school publishes to
  guardians. A section missing from it is not withheld by this server; the
  school does not publish it. An active warning that is not yet published is
  reported as such rather than as something the family has been told.
- **`get_results(student_id?: int)`** — Published results, newest first.

  Says *that* a result was published, for which assignment, by whom and
  when. It does **not** carry the grade: SchoolSoft's results list has no
  such field, and the model has no empty `grade` that would read as "no
  grade given". For the grade, call `get_assessment_detail` and read
  `assessed_work`.
- **`get_open_work(student_id?: int, include_expired? = False, entity_type?: str)`**
  — Everything currently open, in due order, whatever week it falls in.

  `get_homework` answers "what falls in week N", which is right for a day
  briefing and wrong for "what does this child still owe". A task due in
  three weeks is invisible to the week query and present here.

  The list mixes assignments and plannings. Pass `entity_type="ASSIGNMENT"`
  for work with a deadline; term-long plannings otherwise appear with an end
  date in December. Expired work is hidden unless `include_expired` is set,
  and the `note` says how much was hidden.

### Fritids (after-school care)

- **`get_fritids_times(student_id?: int, year?: int, month?: int)`** — The
  care times booked for a child ("Mina tider"): the month's calendar with
  drop-off and pick-up per day, and the detailed week with school hours
  beside the booked times and any comments between home and staff.

  This is the page that decides when somebody has to be at the school to
  collect a younger child, and it only exists in the menu for children
  enrolled in fritids — which is why nothing else knew about it.

  `has_fritids` is false when no day carries a time. Check it before
  reading an empty `pick_up` as "goes home after school": for a child who
  is not enrolled every day is empty and means nothing. A weekday inside
  the term with `booked: false` is a day nobody has arranged care for.

  Defaults to the current month; pass `year` and `month` (1-12) for
  another. Read only — the page can change times, this tool never does.

### Student documents (IUP)

- **`get_student_documents(student_id?: int)`** — Elevdokument: every IUP
  / development-talk document, with a status cell per subject plus
  "Övrigt" and "Allmänt omdöme" saying which of staff, pupil and guardian
  have written their part and which are still awaited. `guardian_pending`
  lists the parts the family is expected to write before the next talk;
  empty is the normal state and worth saying.
- **`get_student_document(doc_id: int, student_id?: int, part_type? = 4, subject_id? = 0)`**
  — One part in full. The defaults select the general assessment, which is
  where the IUP text lives: the goals agreed at the development talk, the
  method and who is responsible, how the child says they are doing, and who
  was in the room — grouped in `blocks` by who wrote them, dated and signed.

  This is the child's own words about school as well as the school's. Quote
  it; do not paraphrase it into something the child did not say. The page
  is named "edit" and renders read-only for a guardian; nothing here posts.

### Grades

- **`get_grades(student_id?: int)`** — Subject grades for the active child
  (Elevdokument → Betyg). Returns one `GradeEntry` per `(subject, term)`
  pair plus the list of `terms` seen. Entries with no grade and no note
  are skipped.

### Attendance

- **`get_attendance(student_id?: int)`** — Per-week attendance summary for the active child
  (Frånvaro → Rapport). Each `AttendanceWeek` carries total presence
  percentage, unreported/reported absence counts, and sub-categories
  (sen ankomst, föranmäld, etc.).
- **`get_unreported_absence(student_id?: int)`** — Unreported absence
  (Frånvaro → Oanmäld frånvaro), split by whether it has been acknowledged.

  SchoolSoft texts a guardian when a lesson is missed and then expects them
  to open that page and confirm they have seen it. Confirmed rows stay on
  the page permanently, in a second table with the same columns, so a
  parser that takes the first table it recognises reports weeks-old,
  handled absences as outstanding — every day, forever.

  `events` holds only what is still awaiting confirmation. `acknowledged`
  holds the rest, with who confirmed and when.
  `confirmed_none_pending` distinguishes "the page said there is nothing"
  from "we parsed nothing".

  This tool only reads. Confirming an absence is the guardian's own
  attestation that they saw it, so it is deliberately not automated: a
  server that ticks it makes the school's record claim a parent took note
  when nobody did.

### News, veckobrev, and attachments

- **`get_news(older?: bool = False, student_id?: int)`** — News and
  veckobrev. Each item carries a `news_id`/`type_id` and a list of
  `attachments` with their `fileid` and `filename`. Set `older=True` for
  the archived view.
- **`get_news_item(news_id: int, type_id?: int = 1, student_id?: int)`** —
  Fetch one news item with the full body and attachments.
- **`download_attachment(news_id: int, fileid: int, type_id?: int = 1, object_kind?: str = "news", student_id?: int, max_bytes?: int = 700_000)`**
  — Download a news or message attachment as base64-encoded bytes. Use
  when you need the raw file (e.g. to save to disk). Otherwise prefer
  `read_attachment_text`, which is far cheaper for LLM context.
- **`read_attachment_text(news_id: int, fileid: int, type_id?: int = 1, object_kind?: str = "news", student_id?: int, max_chars?: int = 50_000, offset?: int = 0)`**
  — Download an attachment and return its extracted plain text. Supports
  PDF (via `pypdf`), `.docx` (via `python-docx`), and plain-text files.
  The right tool for *"vad står det i veckobrevet?"*.

On a multi-child account, pass the same `student_id` to the download that
you passed to `get_news` — attachments are only served for the child
currently selected. A download that still fails after the retry comes back
with an empty payload and a `note` explaining which of the two causes it
was (wrong child vs. a file genuinely missing upstream) instead of raising
a bare 404.

### School information & contacts

- **`get_school_info(student_id?: int)`** — The Skolinformation page rendered as plain
  text (school hours, phone numbers, term dates, addresses, …). The
  page is free-form HTML so we don't impose structure — the model
  picks out what's relevant.
- **`get_contacts(student_id?: int)`** — Classmate / guardian contact list for the
  active child (Skolinfo → Kontaktlistor). Each `Contact` carries
  name, phone (when published) and address. PII-heavy — handle with care.

### Library

- **`get_library_files(student_id?: int)`** — Files shared in the school's library
  (Filer & länkar). Each entry has the display title, clean filename,
  optional description, size, MIME guess, the `request_id` to pass to
  the download endpoint, and the category heading it appeared under.

### Messages

- **`get_messages(student_id?: int)`** — Inbox messages (experimental).

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
  messages). Treat them with the same care as your SchoolSoft account, and
  never paste one into a test fixture — see *Fixtures are written by hand*
  below.

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

scripts/install-hooks.sh          # enable the pre-commit guard
python scripts/check_pii.py       # or run it by hand
```

### Fixtures are written by hand

This repository is public and the system on the other end holds children's
school records. The mistake that has actually been made here is pasting a
real API response into a test because it was already on screen, which
brings a teacher's assignment titles and SchoolSoft's own record ids along
with the payload shape that was the point.

Copy the shape. Type the values.

`scripts/check_pii.py` enforces what it can and runs in CI and as a
pre-commit hook: personal numbers, emails, phone numbers, UUIDs, URLs
naming a specific school, and — the useful one — a reserved id range.
Every SchoolSoft record id under `tests/` must be in **900000-999999**.
Real ids are three or four digits, so a pasted payload fails on its own
ids before anyone reads the text around them.

It also checks a denylist of names, places and the school. That list
cannot live here, because a list of the operator's children's names is
exactly the thing not to publish, so it is read from
`~/.config/schoolsoft-mcp/pii-denylist.txt` or `$PII_DENYLIST_FILE`, one
term per line. Findings print the matched term's length, never the term,
so CI logs stay clean. Without the file the other layers still run and the
script says the name layer is off rather than passing silently.

### Discovering endpoints with Playwright

If you don't know which JSP path a feature lives behind, run the
discovery script to log in and crawl your school's UI in a real browser
while recording every network request:

```bash
pip install -e ".[discover]"
playwright install chromium
# set SCHOOLSOFT_* env vars first (e.g. source a .env), then:
python scripts/discover_endpoints.py
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
