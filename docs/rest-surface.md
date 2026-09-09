# The parent REST surface

SchoolSoft's React parent UI reads almost everything a guardian cares about
from `/<school>/rest-api/<usertype>/…`, where `<usertype>` is `parent`,
`student` or `teacher`. None of it is documented publicly.

## How it was found

The SPA ships sourcemaps. `react/main.<hash>.js` contains the chunk map;
each `react/**/*.js.map` carries `sourcesContent`, i.e. the original
sources. Grepping the chunks for `/ps/` yields the paths below, and the
surrounding component tells you what the payload is for.

```bash
curl -s "$BASE/react/main.<hash>.js" | grep -o 'rest-api[^"]*'
```

This beats watching the network tab: it finds endpoints the pages you
happen to visit never call.

## Wrapped here

These are called by `parsers/subjectrooms.py` and covered by tests against
recorded payloads.

| Path | Payload |
| --- | --- |
| `ps/subjectroom/all` | Every subject room: `activityId`, subject, groups, colour. `activityId` is the join key for everything else. |
| `ps/subjectroom/<activityId>/teachers` | `[{firstName, lastName, id, role}]`. |
| `ps/subjectroom/plannings/grid/rows` | Every planning with real ISO `startDate`/`endDate`, teacher and subject. |
| `ps/planning_parts/<partId>/view` | `{title, description, publishDate, subtitle}`. `description` is the teacher's HTML body — the payload everything else exists to locate. |
| `ps/subjectroom/assignments/grid/rows` | Assignments (läxor, prov, inlämningar) with real dates. |
| `ps/assignments/<id>/view` | An assignment's full text. |
| `ps/material/<partId>/file`, `.../link` | Attachments and links. |
| `calendar/subject_room/exam-schedule` | Announced exams. **The dates are the window the announcement is displayed in, not when the exam is written.** |
| `calendar/lessons/<id>` | Room, teachers and group for one lesson. |
| `ps/subjectroom/results/grid/rows` | Published results. **Carries no grade value** — see below. |
| `ps/subjectroom/table/rows` | Everything open right now, week-independent. Mixes assignments and plannings; `entityType` says which. |
| `holistic_assessment/rows` | Sammantagen bedömning, one row per subject, including `subjectWarning`. |
| `holistic_assessment/options` | `{id, label}` per subject; `label` is the only place the subject code appears. |
| `holistic_assessment/overview` | Any action plan (åtgärdsplan). |
| `holistic_assessment/<id>` | Header: pupil, subject, group, publish status. |
| `holistic_assessment/<id>/sections/published` | Which sections the school publishes to guardians. |
| `holistic_assessment/<id>/knowledge_development/view` | `{value, supportMeasures, updatedByInfo}` — the assessment itself. |
| `holistic_assessment/<id>/formative_comments` | The teacher's written comments. |
| `holistic_assessment/<id>/subject_warning` | The "risk of not reaching the goals" flag and its motivation. |
| `holistic_assessment/<id>/assessed_assignments` | The graded work. **`review` is the grade.** |
| `holistic_assessment/<id>/reporting_occasion_assessment` | The same subject at earlier reporting occasions. |

## Seen but not wrapped

Real paths, never exercised against a live tenant. Wrapping one means
recording a payload and writing the parser test first — an untested
constant that looks tested is worse than no constant at all.

| Path | Apparent payload |
| --- | --- |
| `ps/subjectroom/<activityId>` | One room's detail. |
| `ps/subjectroom/unread_entities` | `{assignments, plannings, results, sum}` for the UI's badges. |
| `ps/subjectroom/<activityId>/plannings/grid/rows` | Plannings scoped to one room. |
| `ps/planning_parts/<partId>/sections` | A part's sections, which is how material ids are grouped. |
| `ps/plannings/<planningId>/view` | The planning above its parts. |
| `ps/plannings/<planningId>/planning_parts/tabs` | The parts a multi-part planning is split into. |
| `ps/material/<partId>/file/<fileId>` | A single file's bytes. |
| `holistic_assessment/<id>/school_support_activities` | Returned `null` on the tenant tested. |
| `holistic_assessment/school_year` | `{start, end}` for the current school year. |

Paths that look plausible and are **not** real: `holistic_assessment/<id>/sections`
(only `sections/published` exists), `.../formative_comment` singular (only the
plural), `.../knowledge_development` without `/view`, `.../attendance`, and
`.../matrix_link`. All 404. They are listed because the bundle mentions them —
they are teacher-side routes.

## Gotchas

- **Week-scoped queries drop term plans.** The start-page planning list
  only returns plannings that *start* in the week asked about, so a term
  plan — exactly the kind carrying week-by-week detail — never appears.
  Select by date overlap instead.
- **The start-page endpoints are teasers.** They carry a title and a glued
  subtitle and no body. The body is one call further in.
- **Results have no grade on the results endpoint.** `results/grid/rows` says
  *that* a result was published, by whom and when. The grade itself is on the
  subject's assessment, in `assessed_assignments[].review`. A model that reads
  the results list and reports "no grade" is reading a field that does not
  exist rather than an empty one.
- **The assessment list mixes assessed and unassessed subjects.** A row can be
  present with `published: false` and "Ingen bedömning publicerad". Counting
  those as unread invents work for a guardian who has none.
- **The bundle to grep is `react/main.<hash>.js`, not `react/jsp/main.<hash>.js`.**
  The latter is the shell embedded in the legacy JSP pages and only knows two
  `ps/` paths. The parent SPA's own entry is at `/<school>/react/`, and its 477
  chunks are where the rest of the surface lives.
- **A parent session has a selected child.** Endpoints answer for whichever
  child was last selected, with a 200 either way, so every tool here sends
  `student_id` and selects first.
