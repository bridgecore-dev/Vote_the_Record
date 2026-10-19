# Vote the Record — Project Rules

A nonpartisan U.S. voter information project. It starts with Harris County, Texas and is
built to scale to every state. These rules apply to all code, data, and content.

## 1. Official records first
Use primary government sources before anything else:
- Local: city council minutes/votes, commissioners court minutes/votes, agendas
- State: legislature journals and roll-call votes (e.g., Texas Legislature Online)
- Federal: congressional roll calls (Congress.gov, House Clerk, Senate)
- Elections: secretary of state and county election filings, candidate lists
- Campaign finance: FEC, state ethics commissions (e.g., Texas Ethics Commission), local filings

Use news reports only when no official record covers the fact, or to add context to one.

## 2. Every fact is labeled, linked, and tracked
Every fact carries:
- `id` — globally unique and path-based: the file's path under `data/states/` without
  `.yaml`, plus `#` and a local ID. Example:
  `tx/localities/harris-county/elections/2026-11-03/commissioner-precinct-2/candidates/jane-doe#F-001`
  IDs never change once published. The validator checks that the prefix matches the file's location.
- `label` — `official_record`, `news_report`, `candidate_claim`, or `organization_statement`
  (an organization speaking for itself on its own site, e.g. an endorsement announcement;
  display-only, never allowed on scorable `records`)
- `claim_status` — `documented`, `allegation`, `disputed`, or `contradicted`.
  `contradicted` requires a non-empty `contradicted_by` list of fact IDs.
- `source_url`, `source_title`, `event_date`, `retrieved`
- `source_kind` — `document` (a static file such as a PDF) or `page` (a web page)
- `sha256` — hash of the downloaded file, for `document` sources only; always null for `page`
  sources, which rely on `archive_url` instead
- `archive_url` — Wayback Machine preferred (archive.today only if Wayback fails)
- `verification` — `unverified` or `verified`
- `verified_on`, `reviewer` — null until verified

A verified fact must have `archive_url`, `retrieved`, `verified_on`, and `reviewer`, plus
`sha256` if it is a document, and `verified_on` may not be earlier than `retrieved`.
The validator enforces this.

## 3. No claim without a source
No fact, summary, or characterization gets published without a source. Unsourced items
don't get merged; the validator rejects them. Don't infer or extrapolate past what the
source says. If a source can't be retrieved, record that in `notes` and leave the fact
unverified; never fill in details from memory.

## 3a. Only the project owner verifies
Collectors, scripts, and AI assistants (including Claude) may only write
`verification: unverified` with `verified_on: null` and `reviewer: null`. Only the project
owner sets `verified`, `verified_on`, and `reviewer`, on facts and on mappings alike.
Never change these fields on an existing entry. The site shows an "Unverified" badge on
every unverified item.

Claude may write under `data/` without asking first (owner's decision, 2026-10-18). Every report
lists what changed under `data/` so the owner can review it before saying "commit".

## 4. Survey questions are hypotheticals tied to the office's powers
Survey questions are hypothetical scenarios that the office could actually face. Each one
must cite the specific power ID(s) it rests on, from that office type's `powers.yaml`.
No questions about matters the office has no authority over, and no questions built from
party platforms or hot-button issues outside the office's scope. A scenario written around one
state's laws lists it in `states` (e.g. `[TX]`); a race page asks a scenario only in those states
and only when every power it cites applies to that race.

Every survey option carries `tags` from the office type's tag list; tags never name or
imply a party. Each mapping links one record to one question and lists the `options` it
supports (one or more), with a written `rationale`. Mappings carry their own
`verification`, `verified_on`, and `reviewer`.

## 5. Party is ballot information only
Show party only as it appears on the ballot ("Listed on ballot as: ___"), stored in the
nullable `ballot_party` field. Never use it to sort, score, rank, color-code, or describe
candidates, and never mention it in scenarios.

## 6. Never commit secrets
Never commit API keys, tokens, or credentials. Keep them in a local `.env` (gitignored)
and list only the variable names in `.env.example`. Check staged files before every commit.

## 7. Scoring follows docs/METHODOLOGY.md exactly
Weights: vote 3, sponsored 2, promise kept/broken 2, statement 1; multiplied by user
importance (1–3) and evidence factor (official 1.0, news 0.7, claim 0.4). Only facts that
are both verified and documented, and mapped through verified mappings, are scored.
Records sharing a `same_event` group that map to the same question count once: only
the highest-weight one is scored. Questions with no scorable record are excluded, never
scored zero. Results always show "Based on X of Y questions." A match percentage appears
only when at least 3 answered questions have scorable records. Otherwise the site is in
beta mode for that candidate: it shows a per-question breakdown with verification
badges and no overall percentage. The promise tracker is display-only and never affects
the score. For now it applies to incumbents only (the validator rejects it on others); it
shows counts by status and a kept rate (kept ÷ decided, once at least 3 are decided), and
no letter grade. Any change to weights or formula requires updating METHODOLOGY.md.

## 8. Corrections are public and owner-maintained
The public submits corrections through the corrections form (`/report/`, stored privately
by the Worker in `src/worker/`; reports are never committed or published). The project owner reviews
them and adds entries to `corrections/log.yaml` by hand. Collectors and AI assistants
never write to the corrections log. Entries are never deleted.

## Neutral language
Use the same wording and structure for every candidate. Avoid loaded adjectives. Report
votes and filings as they are recorded.

## Repository layout
- `src/` — code only (collectors, validator, build). Python.
- `schemas/` — JSON Schema definitions (fact, candidate, race, actions, office powers,
  survey, locality, corrections).
- `offices/<office-type>/` — reusable, state-agnostic `powers.yaml` + `survey.yaml`.
- `data/states/<st>/` — data only, in YAML.
  - `office-overrides/<office-type>.yaml` — state-specific powers, each citing the statute.
  - `statewide/elections/<YYYY-MM-DD>/<race>/`
  - `districts/<district>/elections/<YYYY-MM-DD>/<race>/` — races by district (e.g.
    `us-house-7`, `state-house-134`, `court-of-appeals-1`), which can cross counties.
  - `localities/<county>/elections/<YYYY-MM-DD>/ballot.yaml` — every contest on that county's
    ballot, in ballot order, citing the sample ballot; `zips.yaml` beside it maps ZIP codes
    to voting areas (a nested list = the ZIP is split), and `precincts.yaml` maps voting
    precincts to their areas exactly. `everywhere` lists areas every ZIP/precinct gets.
    `precinct-shapes.json` holds simplified precinct outlines for the address lookup, and
    `area-shapes.json` the outlines of cities, school districts, and water districts.
    `ballot.yaml` `unmapped` lists districts with no usable boundary: lookups never place a
    voter in them; the page lists them separately with the county's lookup link.
    Generated per county by `src/collectors/states/tx/county_precincts.py --county <slug>`.
  - `localities/<locality>/elections/<YYYY-MM-DD>/<race>/race.yaml` + `candidates/<slug>.yaml`
  - `localities/<locality>/actions/<YYYY-MM-DD>.yaml` — official body actions from one
    meeting (what passed), which candidate vote records point to via `same_event`.
  - Localities are flat under the state (a city can span several counties).
- `docs/` — METHODOLOGY.md (public explanation of sourcing and scoring).
- `site/` — the public front end.
- `.github/workflows/` — CI: runs the validator on every push and PR.
- `corrections/` — public corrections log (`log.yaml`, owner-edited) and policy.

## Commands
- Install: `python -m venv .venv` then `.venv/Scripts/pip install -r requirements-dev.txt`
- Validate all data: `python src/validate/validate.py`
- Tests: `python -m pytest tests`
- Build the site: `python src/build/build.py` (stops without writing if validation fails)
- Preview: `python -m http.server 8000 -d site/dist`, then open http://localhost:8000
- Demo race pages (fictional data, never deploy): `python src/build/build.py --root tests/fixtures/demo --out site/dist-demo --demo`
- Read "Report a problem" submissions (owner only): `python src/review/reports.py`
- JS scoring tests alone: `node --test "tests/js/*.test.mjs"` (also run by pytest via tests/test_js.py)
- Site settings and deploy details: `site/README.md`
- Upkeep (runbook: `docs/MAINTAINING.md`; all preview first, `--write` to apply):
  `python src/tools/status.py [--check-links]`, `python src/tools/check_rosters.py`,
  `python src/tools/archive.py`, `python src/tools/new_election.py --spec <file>`,
  `python src/collectors/states/tx/tec_finance.py --ballot <ballot.yaml> (--zip <file> | --download)`.
  The weekly `upkeep` workflow posts the same checks to a GitHub issue; it never commits.
- Owner's review page (verify facts with one click; owner only, never run by Claude): `python src/review/review.py` — see `docs/REVIEWING.md`

## Site rules
- No inline styles, inline scripts, or event-handler attributes: the CSP forbids them.
- No third-party scripts, fonts, analytics, cookies, or storage.
- Candidates always render in alphabetical order, never by score.
- Each candidate card leads with "On the record": recorded votes first, then other actions,
  newest first within each group; the same rule for every candidate.
- Every unverified item shows an "Unverified" badge.
- Ballot pages order races by level of government, closest to home first (local, county,
  state, federal; `level` in each office's `powers.yaml`), then by official ballot order.
- The ZIP and precinct lookups run in the browser only. The address lookup sends the address to
  our Worker (`POST /api/locate`), which asks the Census geocoder and returns only the location
  and county; the browser finds the precinct from `precinct-shapes.json`. Never store or log
  what a visitor types, and never send it anywhere else. The CSP allows `connect-src 'self'`
  only. The Worker module may export only functions and objects (strings break the runtime).
- Ballot pages offer topic highlights (each power's `topics`, from what the law says the office
  does) and one quiz across the visitor's races. "Closest to your answers" appears only when
  at least two candidates have records, every candidate with records has a match percentage,
  and one is strictly highest. A candidate with no record found is listed, said so plainly,
  and left out of the comparison. Offices
  with `judicial: true` never have quiz questions (Code of Judicial Conduct, Canon 5). Results
  go out only by print/PDF or a link carrying answers after `#`; nothing is stored or sent.
- A race with `detail: basic` says "We haven't researched this candidate's record yet",
  never "Not found in the sources reviewed". `incumbent: null` means not checked yet.
