# Keeping the site up to date

This is the runbook for keeping Vote the Record current: every week, for each new election,
and after Election Day. Every tool here previews its changes first. Add `--write` only after
reading the preview. Any change under `data/` asks for the owner's approval, and no tool ever
marks anything verified (see [REVIEWING.md](REVIEWING.md)).

Run commands from the project folder in a VS Code terminal (**Terminal → New Terminal**).
On Windows, write `.venv\Scripts\python` where this page says `python`.

## Every week

A GitHub Action (`.github/workflows/upkeep.yml`) runs every Monday morning. It updates one
GitHub issue labeled **upkeep** with:

- **Status:** unverified items, sources without an archived copy, races missing incumbent
  status or money information, and source links that stopped loading.
- **Rosters:** official officeholder lists that no longer match who we show as the incumbent.
- **Campaign finance:** new Texas Ethics Commission reports since the last update.

It never changes anything. To act on it:

| The report says | Run |
|---|---|
| Sources without an archived copy | `python src/tools/archive.py`, then again with `--write` |
| New campaign finance reports | `python src/collectors/states/tx/tec_finance.py --download --ballot <ballot.yaml>`, then again with `--zip cache/tec/TEC_CF_CSV.zip --write` |
| A roster no longer matches | Open the roster link and look. If a seat changed hands, update that race by hand. If it's a known, harmless case (say, a candidate holding a *different* seat on the same list), add it to `src/tools/roster_known.yaml` with the reason. |
| A source link stopped loading | Nothing urgent: the archived copy still holds the record. Find the document's new address if there is one. |

To run the same checks on your computer:

```
python src/tools/status.py --check-links
python src/tools/check_rosters.py
```

New money reports never replace old ones. Each report becomes its own fact (M-01, M-02,
...). The race page shows the newest, and earlier ones sit under "Earlier reports".
Each money fact cites that report's own PDF, as filed with the Texas Ethics Commission, with
its SHA-256, after the tool checks that every total appears in it. Archive the new PDFs with
`archive.py` so they can be verified. A report whose PDF is missing or doesn't match is listed
as "BY HAND" and isn't added.

## A new election

1. **Get the official sample ballot** from the county clerk or elections office. Save the
   PDF, note its URL, and get its SHA-256 (`certutil -hashfile file.pdf SHA256` on Windows).
   Archive it: `https://web.archive.org/save/<url>`.
2. **Write a spec file** listing every contest in ballot order, with names exactly as printed.
   The format is at the top of `src/tools/new_election.py`. Example:

   ```yaml
   ballot: tx/localities/travis-county/elections/2027-05-01
   name: Travis County ballot
   election_date: "2027-05-01"
   election_label: May 1, 2027 joint election
   lookup_url: https://...
   sample_ballot: {issuer: Travis County Clerk, url: "https://...", title: "Sample Ballot, May 1, 2027",
                   sha256: "...", archive_url: "https://web.archive.org/web/...", retrieved: "2027-03-15"}
   contests:
     - race: tx/localities/city-of-austin/elections/2027-05-01/council-district-3
       name: Austin City Council, District 3
       office_type: city-council-member
       page: 2
       candidates: [{name: JANE DOE, party: null}]
   ```

3. **Create the files:** `python src/tools/new_election.py --spec <spec.yaml>`, check the list,
   then add `--write`. Every race starts as a basic page. Nothing existing is overwritten.
4. **New office types** need `offices/<type>/powers.yaml` (with `level`, and `judicial: true`
   for any judge) and, for Texas, `data/states/tx/office-overrides/<type>.yaml`, with each power
   citing its law and listing its `topics`.
5. **Address and ZIP lookup:** run `src/collectors/states/tx/county_precincts.py --county <county>`
   for the new ballot, writing to a scratch folder first, then copy the four files into the ballot
   folder. A county not yet in its `COUNTIES` table needs an entry: its official precinct layer,
   the field for each district kind, the areas every voter shares, and its cities, school
   districts, and water districts (Census and TCEQ IDs). Districts with no official boundary go in
   the ballot's `unmapped` list.
6. **Same data for every race:** incumbent status from official rosters (add a
   "Who holds this office now" fact, `#S-HOLDER`, citing the roster), and campaign finance with
   `tec_finance.py`.
7. **Check:** `python src/validate/validate.py`, `python -m pytest tests`, and
   `python src/build/build.py`. Preview with `python -m http.server 8000 -d site/dist`.
8. **Review** the new facts in the review page ([REVIEWING.md](REVIEWING.md)), then merge to `main`
   to publish.

Ballot pages disappear from the site once their election date passes. Race pages stay.

## Election night and the canvass (results)

Each race or measure gets a `result.yaml` beside it (schema `result` in `schemas/other.schema.json`), shown on
`/results/` (one page per county ballot) and at the top of the race page. Candidates stay alphabetical.

1. **Get the county's results report** from its official results page. The collector reads:
   - a Clarity ENR `summary.zip` (or the `summary.csv` inside it): Bexar, Comal, El Paso, Fort Bend,
     Tarrant, Travis;
   - a Hart "Cumulative Results Report" PDF, in either text layout: Harris, Tarrant, Brazoria, Brazos,
     Ector, Hays, Nueces, Denton;
   - an ES&S "Summary Results Report" PDF: Bell, Bexar (official), Dallas, Williamson, Webb (probably);
   - a precinct-by-precinct CSV with a `COUNTY TOTALS` row: Collin;
   - any CSV or JSON with contest, choice, and vote columns.

   Clarity, and several county sites, refuse scripts; save those files in the owner's browser (the
   `claude --chrome` session), the same way as sample ballots.
2. **Preview, then write:**
   `python src/collectors/results.py --ballot <county ballot.yaml> --file <report> --source-url <report URL>
   --source-title "<County>: Unofficial Results, ..." --as-of <report date> [--source-kind document]`, then
   again with `--write`. It matches contests by candidate names (measures by proposition label and place),
   lists anything it can't match (settle those with `--map "Contest as printed=<contest path>"`), and never
   replaces an official or verified result.
3. **Coverage:** a county's report fills in its own county contests in full, and its city, school, and
   special-district contests (marked "partial" with the county's name; pass `--whole <path>` for one that lies
   entirely inside the county). It leaves statewide and district races alone: their totals come from the
   Secretary of State, with `--statewide TX 2026-11-03` in place of `--ballot`. A partial count never replaces
   a whole one, or another county's partial one.
4. **After the canvass:** rerun with `--status official` and the canvass report. Only then add `outcome`
   (`elected`/`runoff`, or `passed`/`failed`) to each `result.yaml` by hand, from the canvass.
5. Validate and build as usual; every result shows an Unverified badge until the owner checks it.

## After Election Day (design, to build once results are certified)

After the election, the site shifts from candidates to the people who won: what they said,
what they do, and how they vote, next to each other and sourced. The plan:

1. **Results.** Done for vote counts (above). Next, the ballot page becomes an archive page that
   shows outcomes.
2. **People.** Add a person record, `data/states/<st>/people/<slug>.yaml`, so that someone who
   runs again, or wins and later runs for another office, keeps one history. Candidate files
   point to their person. Records and promises then belong to the person, not to one race.
3. **Officeholders.** Each winner gets an officeholder entry for the seat and term. Their votes
   and actions (from official minutes and roll calls, in the existing `actions/` files) and
   their promise tracker carry over from the campaign.
4. **What they said vs. what they did.** The officeholder page shows each promise or statement
   next to the matching votes and actions, with sources. Like everything else, it states what is
   recorded, in the same words for everyone, with no labels or adjectives.
5. **Later:** shareable cards built from the same sourced facts, for social media.

Open questions to settle before building: how person IDs are chosen, and how to link records
gathered for a candidate page to the person without changing any published fact ID.
