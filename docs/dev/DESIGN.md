# How ReviewDesk is built: design and rules for contributors

ReviewDesk runs a systematic review for a small team: the search log, import and de-duplication, title and abstract
screening (a calibration round, an agreement sample with Cohen's kappa, and optional single screening with an AI
second screener that never excludes), full-text review, data extraction, risk of bias, GRADE, and the PRISMA 2020
reporting files. It runs on each person's own computer (a Python standard-library web server on 127.0.0.1) and opens
in the browser; the review lives in a project folder the team shares through an ordinary sync service.

**Its users are not programmers**: students, clinicians and their supervisors. Everything they read must be plain,
specific British English, and nothing may need installing.

## Rules that every change must keep

### Code

- **Python standard library only**, and it must run on **Python 3.9** (every module starts with
  `from __future__ import annotations`; no `match`, no 3.10+ library calls). Plain JavaScript ES modules; no
  libraries, no build step, nothing loaded from the internet.
- **The browser rules** (Content-Security-Policy) forbid inline scripts and anything not served by ReviewDesk. Build
  pages with `h()` from `static/lib.js`; never put text from records, settings or files into `innerHTML`. SVG made by
  the server that escapes its own text (see `prisma.py`) may be inserted with `{ html: svg }`, as the PRISMA page
  does. The `html` attribute of `h()` is the one place a page sets markup, and the static analysis stops at it: a new
  use of it needs every piece of text escaped where the markup is made, and a test that hostile text stays text
  (`tests/test_security_ui.py` is the one for a record's title and abstract).
- **Who writes what.** Almost every file in a project folder has one writer, so a sync service never has to merge two
  people's edits (the layout is at the top of `project.py`). Each person writes their own work only in their own
  files (`<folder>/<INITIALS>@<computer>.jsonl`, append-only, through `integrity.append`, as `review._write` does);
  only the project lead writes the shared files (`p.update_settings`, `p.save_state`, `review._write_agreed`). The
  exceptions, by design: the search worksheet (`searches/worksheet.json`), which any member may save, with a
  revision check that refuses a save made over someone else's; and new files in `imports/` and `fulltexts/`, which
  anyone may add. Every action that matters goes in the audit log (`p.audit`).
- **What the data checks are for.** The chained fingerprints and this computer's copies notice accidents and sync
  faults (rows edited, lost or put back older) on a computer that has seen the rows; the per-row signature can be
  checked only by the computer that wrote the row. The fingerprints use no secret and a row's author is the name
  of its file, so the checks do not stop a team member who deliberately writes rows in another person's name.
  Documents must not claim more than this (SECURITY.md says it for users). New per-person data must be covered by
  the data checks the way `rob` and `extraction` are (read `project.py`: `_heal_own_files`, `integrity_report`, the
  `subs` lists) and by the de-duplicating readers in `review._rows`.
- **A fingerprint is a whole SHA-256, and a signature a whole HMAC-SHA-256.** Anything kept in a project so that a
  later change can be noticed is made by `integrity.row_hash`, `sign`, `record_print` or `text_print`, and never cut
  short. Versions before 1.3.1 kept the start of each, and those are still in the projects of that time: so a kept
  fingerprint is never compared with `==`, but with `integrity.same` or `integrity.follows`, which accept one of the
  old length only where an earlier version can have written it (the notes at the top of `integrity.py` say where;
  `tests/test_fingerprints.py` holds them to it). `integrity.label` is for names, not checks: what a problem in the
  data checks is called stays as short as it always was, so that problems already accepted are still recognised.
  A change to what is fingerprinted, or how, is a change an older version cannot check: raise `versions.NEEDS`.
- **Never compare two computers' clocks.** Within one file the order written decides which row is the latest
  (`project.written_order`); time stamps only interleave one person's files from two computers. What counts for
  agreement is decided by the mark each vote carries of what its voter could already see (`screening.cast`,
  `compare_state`, `independent`), not by comparing times. The screening state and the risk-of-bias plan carry a
  revision number, raised at every save (the plan's is logged with its audit entries), and "which is newer" is
  decided by it; a file without one is compared only with versions saved on the same computer.
- **One team, one version.** `versions.NEEDS` is the oldest ReviewDesk that can read everything this one writes.
  Raise it, in the same change, whenever a change to the files would mislead or stop an older version: that is all a
  new version has to do. The project file says which version the project needs (`min_version`, written when the lead
  saves it, and when the lead opens the project with a version that needs more than it says), and an older ReviewDesk
  refuses to open it. Every audit entry says it too (`needs`), and everyone leaves one on first opening a project
  with a version, so an older ReviewDesk stops saving in a project once it has the entry of anyone who has worked on
  it with a version it cannot read (`versions.must_upgrade`, enforced for every request that changes something; it
  does not repair its own files then either, `Project._heal_one`). Both are files, and reach another computer only
  when the sync service brings them: until then an older version knows nothing, which is why UPGRADING.md has every
  ReviewDesk closed first and the lead go first. A version already released can never be taught anything, so these
  checks must not be weakened, and a new kind of record must never reach an older version by a route that passes
  them by.
  Something only the lead can switch on, and that an older version would misread, need not raise `NEEDS` for every
  project: it raises what *that* project needs at the moment it is switched on (`versions.FEATURES`; `versions.stamp`
  for a setting, `Project.need_version` for anything else, called BEFORE the new kind of record is written), and
  from then on every audit entry of the project declares it (`versions.declares`). Time recording and the
  verification sample work this way. (1.3.1 then raised `NEEDS` itself, for the whole fingerprints, so in the end
  neither raises anything: 1.3.0 and 1.3.1 cannot share a project.)
- **Every request that changes something has a shape** in `shapes.ROUTES`, checked before anything is saved: a new
  POST route needs its entry there. What a request stores syncs to the whole team, and one value of the wrong kind
  in an append-only file could stop every page for everyone.
- **A request refused before its body is read is still read to its end.** The server answers a request it will not
  act on (no session token, another site's page, a file over the limit) without reading what came with it. If the
  connection then closed with that unread, the computer would break it off, and on Windows the answer can be lost
  on its way: the page would say ReviewDesk is not running instead of the reason. Any such answer goes through
  `Handler._discard_body` (`server.py`), which reads and throws away what is still arriving: all of it from one of
  ReviewDesk's own pages, a bounded amount for a bounded time from anything else.
- **Blinding.** Nothing may show one reviewer's votes, answers or judgements to the other before both have finished
  the piece of work they share (see `server.download_blocked`, `review.rob_view`, `h_agreement`). The AI's answers
  are shown only to the project lead, and only after the agreement sample; a reviewer who is not the lead never
  receives anything derived from them. Any new page, route or download must follow the same rules: check them on the
  server, not only by hiding a button.
  A verification sample (`evaluation.py`, whose notes list what keeps it blind) adds five rules. Its votes change
  nothing until it is finished (`screening.verify_finished`): otherwise every count and list would tell its verifier,
  after each Include, whether that record had been excluded. Until then its verifier, the lead included, is refused
  every route and file that lists records by what became of them (`server.VERIFIER_CLOSED`, `evaluation.blocked`): a
  new route that names records at or beyond full text belongs in that list (a test in `tests/test_evaluation.py` fails
  until every new route has been sorted, open or closed), and anything smaller that says a record reached full text
  (it has a PDF, a free copy was found for it, a data check names it, the AI kept it) is left out of what its verifier
  is sent. The draw is logged whole in the audit log of the lead who made it, and the latest logged draw is the sample
  in force whatever the state holds (`screening.verify_logged`: the log only grows, the state can be put back), so
  putting back an older state opens nothing, an ended sample stays ended, and nothing is drawn twice; with no logged
  draw either, the routes stay closed to whoever has votes on a sample until it is drawn again
  (`screening.verify_duty`). The vote that finishes a sample is taken only with `finish`, which the page sends after
  asking. Nobody, the lead included, is given its votes or figures before it is finished (`evaluation.open_sample`).
  And only the lead is given the numbers it was drawn with, in pages, text and the audit log alike: from them a
  reviewer could work out how many of their exclusions the AI kept. And its votes are fixed the moment it is finished
  (`screening.verify_votes`): until then its verifier has seen nothing and may change any vote; from then on the pages
  are open to them, so a later vote is refused, and one that reaches the files all the same never counts (the rule
  reads the files, so every computer gives the same answer). A verifier's vote can send a sampled record on and do
  nothing else.
- **Timing people is their choice.** Time recording (`timing.py`) is off unless the lead switches it on; the pages
  that are timed say so; each person can leave their own votes out from those pages; a page measures nothing when
  recording does not apply to its user, and the server keeps a time only when both the project and the person say
  yes. Times never affect a record and are shown on no page: they leave ReviewDesk only in the lead's evaluation
  download, whose copy for sharing names nobody. Measure nothing more than `timing.py` lists without saying so on
  the page and in the read-me of that download.
- **The AI never excludes.** A record is kept unless every model excludes it, a failed or unreadable answer counts
  as kept, and a person decides every record. Only the review question, the criteria and each record's title,
  abstract, year, journal and publication type are ever sent to the AI service.
- **Reports state only what was recorded.** A sentence in an output (the methods text, the tables, the AI report) is
  either something ReviewDesk recorded, with its number, or a highlighted fill-in for the authors. Singular, zero and
  plural must all read correctly.
- **Tests** use pytest, never the network, and never a real AI service or key (`tests/mock_elm.py` is a stand-in
  that runs on the same computer). Add a test for everything you change, and run the whole suite before you send it.
- **Static analysis** has nothing to report: Ruff for the Python (`ruff.toml`), ESLint for the pages
  (`eslint.config.mjs`), and CodeQL on GitHub. When a tool is wrong about a line, the line says so and why
  (`# noqa: <rule> - why`, `// eslint-disable-next-line <rule> -- why`); a rule is switched off for the whole project
  only with its reason written beside it in the settings file. CONTRIBUTING.md says how to run them.

### Words on screen

- Plain, specific British English; sentence case; active voice; say exactly what happens and what to do next:
  "Choose the second reviewer (Project set-up, People)", not "Configuration incomplete".
- Name things as the user knows them (records, votes, the agreement sample, the project lead), never as the code
  does. Explain a term the first time it appears (kappa, calibration, PRISMA-S, GRADE).
- An error or a missing item says what is missing, why it matters and where to put it right, with a button or link
  that goes there. Never show an exception or a status code.
- Buttons say what they do ("Save and continue", "Start screening"), and the message afterwards says what happened.
- Accessibility: every control has a label; everything works from the keyboard; focus is always visible; colour is
  never the only signal; contrast is at least 4.5:1; pages work from 320 px wide.

## Where things are

| Part | Files |
|---|---|
| Starting up, the session token, the routes and downloads | `main.py`, `server.py` |
| The project folder, records, settings, who may write what | `project.py`, `integrity.py`, `userconfig.py`, `util.py` |
| Which version a project needs, and who in the team uses which | `versions.py` |
| The shape of every request that changes something | `shapes.py` |
| Reading database exports; de-duplication | `parsers.py`, `dedup.py` |
| Screening, agreement and its statistics | `screening.py`, `stats.py` |
| The verification sample; the evaluation download; time recording | `evaluation.py`, `timing.py` |
| The AI second screener | `ai.py` |
| Full texts and the open-access PDF finder | `fulltext.py` |
| Extraction and risk of bias; the tool library and the plan | `review.py`, `robtools.py`, `robplan.py` |
| GRADE | `certainty.py` |
| The steps of a review, in order; what is missing; the timeline | `workflow.py`, `readiness.py`, `timeline.py` |
| The PRISMA diagram and every reporting output | `prisma.py`, `exports.py`, `reporting.py`, `docx.py` |
| The pages | `static/app.js` (routes and state), `static/lib.js` (`h()`, dialogs, requests), `static/views_*.js` |

- `workflow.py` holds **the steps of a review, in order** (`STEPS`, `PHASES`, `steps_for(settings, n_records)`).
  Everything that lists steps (the guided set-up, the checks of what is missing, the timeline, the guides) uses it;
  extend it rather than copying it.
- A back-end module that adds routes has `API_GET` / `API_POST` tables whose handlers take
  `(project, me, query, body)`, and `DOWNLOADS = {kind: (file name, MIME type, make(project, me) -> bytes,
  why_not(project, me) -> "" or a reason)}`. `server.py` registers them (search for `MODULES`); downloads are
  fetched from `/download/<kind>`.
- A project made by an older version must still open: new settings get their defaults in
  `project.DEFAULT_SETTINGS`, and nothing an older version recorded may be dropped or shown differently.

## Starting ReviewDesk: options and settings

`python3 app/run_reviewdesk.py` takes two options, and the start files in `launchers/` pass on whatever they are
given:

| Option | What it does |
|---|---|
| `--port N` | Listen on port N. Without it ReviewDesk uses 47821, or any free port if another program has that one (if that program is ReviewDesk itself, it is opened instead, or ReviewDesk says that another version is running and stops). `--port 0` takes any free port; with a port that is taken, ReviewDesk says so and stops. |
| `--no-browser` | Do not open the web browser. The address to open, with its session key, is printed either way. |

ReviewDesk listens on 127.0.0.1 only, whatever the port. Its own pages are the only intended user of what it answers
there: the requests are not an interface for other programs, are refused without the session key, and may change
from one version to the next (`shapes.py` lists every request that changes something, and what it must look like).
What other programs can rely on are the files: the database exports ReviewDesk reads (the README names the formats,
and sections 5 and 6 of the user guide say how to export and import them) and the files it writes (section 13 of the
user guide; the reporting pack's "Read me first" file says what each one is, and the read-me in the evaluation data
describes every column).

These environment variables are for development and testing; none is needed in normal use:

| Variable | What it does |
|---|---|
| `REVIEWDESK_HOME` | Keep this computer's settings, session key and copies for the data checks in this folder instead of the usual place. The tests give every stand-in "computer" a folder of its own this way. |
| `REVIEWDESK_URL_FILE` | Once started, also write the address (with its session key) to this file. |
| `REVIEWDESK_NO_REOPEN` | Set to `1`: do not reopen the project that was open last time. |
| `REVIEWDESK_DEBUG` | Print each request to the terminal. |
| `REVIEWDESK_AI_URL`, `ELM_API_KEY`, `REVIEWDESK_OA_BASE` | Replace the AI service's address, give a key to use when none is saved, and send the PDF finder's searches elsewhere: see "Settings for developers" in SECURITY.md before using them. |

## The tests' reference review

Most tests run one invented review: prospective evaluations of AI triage tools in emergency departments, by an
invented team of two (`tests/helpers.py`: `reference_project`; `tests/realistic.py` makes its database exports, with
the truth about every study in them, so every number ReviewDesk reports can be checked against an independent count).
`tests/demo_review.py` builds a small finished review in one call, for tests of pages and reports. The guides'
screenshots come from another invented review (`tools/make_guide_screenshots.py`). No real review's records,
decisions or people are in this repository, and none should be added.

## Before you send a change

- `python -m pytest` on the oldest and newest Python you have (the project's own checks use 3.9, 3.15 and the 3.14
  that the Windows download carries; `python -m pytest -m slow` adds the 3,000-record runs).
- `ruff check` and `npx eslint app/reviewdesk/static` report nothing (CONTRIBUTING.md says how to install them).
- `python tools/check_reports.py` makes every Word document from the demonstration review and checks each one opens.
- If you changed a page: open it in a browser as the project lead and as a reviewer, with the keyboard alone and at
  a narrow width, and check the browser's console shows no errors. `tests/test_every_page.py` opens every page in
  Chromium when Playwright is installed.
- If you changed words the guides quote, or anything their pictures show: `python tools/make_guide_screenshots.py`
  remakes the pictures, and the guides in `docs/` need the same change.
