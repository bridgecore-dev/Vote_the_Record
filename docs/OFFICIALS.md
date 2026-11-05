# Officials: who holds each office, and what they do

After the November 3, 2026 election the site follows the people in office, not just the
candidates. Every elected office gets a seat record, and every person who holds one gets a
profile carrying the same kinds of sourced facts as a candidate card: recorded votes and
actions, what they said, campaign finance, and a promise tracker. The CLAUDE.md rules apply
unchanged: official records first, every fact sourced and labeled, everything written by a
collector or assistant unverified until the owner checks it, party only as printed on a ballot.

## Files

**Seat (office):** `data/states/<st>/<scope>/offices/<seat>.yaml`, where `<scope>` is
`statewide`, `districts/<district>`, or `localities/<locality>`, the same places races live.
Example: `tx/localities/harris-county/offices/commissioner-precinct-2.yaml`.

```yaml
name: County Commissioner, Precinct 2      # the office as the government names it
office_type: county-commissioner           # offices/<type>/powers.yaml
area: harris-county/commissioner-2         # optional; same areas as races
holders:                                   # oldest first; those with no end hold it now
- person: adrian-garcia                    # data/states/<st>/people/adrian-garcia.yaml
  start: null                              # only from a source; null if the roster gives none
  end: null
  basis: tx/localities/harris-county/offices/commissioner-precinct-2#S-01
sources:                                   # facts, IDs prefixed with this file's path
- id: tx/localities/harris-county/offices/commissioner-precinct-2#S-01
  headline: Who holds this office now
  statement: The official list (Harris County Elected Officials), checked on ..., lists ...
  ...
```

A board elected at large (MUD directors, at-large trustees) is one seat file with a holder for each
member. A seat with nobody listed on its roster has `vacant: true` and no holders. A winner of an
election who has not taken office yet is added after the canvass, with `start` set to the
day the term begins (from the canvass or the law setting the term).

**Person:** `data/states/<st>/people/<slug>.yaml`. One file per person, whatever offices
they hold or run for, so their history stays in one place.

```yaml
id: adrian-garcia
name: Adrian Garcia
candidacies:        # candidate files from past races; their records show on the profile
- tx/localities/harris-county/elections/2026-11-03/commissioner-precinct-2/candidates/adrian-garcia
research: pending   # optional: nobody has researched this person's record yet
summary: []         # biographical facts
records: []         # votes, sponsored measures, actions in office (record_type as for candidates)
statements: []      # what they said in office (speeches, statements, posts on official accounts)
funding: []         # campaign finance reports
promise_tracker: [] # promises (from candidacies or statements) against what they did; display only
```

Fact IDs in a person file start with `tx/people/<slug>#`. Facts already published in a
candidate file keep their IDs: the profile lists them from `candidacies`, so nothing is copied
or renamed.

**Person IDs.** The slug of the name as the official roster prints it, lower case, without
titles ("Judge", "Hon.") or punctuation: `lina-hidalgo`. If the slug is taken by a different
person, add the place of the office: `david-cook-tarrant-county`. Once published, an ID never
changes, even if the person's name does (the file's `name` changes instead).

## Pages

- `/officials/`: every state and place with seat records.
- `/officials/<st>/<place>/`: each seat in one place (a county, city, school district, district,
  or statewide) with who holds it now, grouped by level like ballot pages. A county's page also
  lists the cities, school districts, and special districts inside it.
- `/people/<st>/<slug>/`: one person's profile. It leads with "On the record" (recorded votes
  first, then other actions, newest first), then what they said, promises, campaign finance,
  the offices they hold or held, and their past races. A profile with `research: pending` says
  "We haven't researched this official's record yet."

## Order of work

1. **Seats and holders everywhere**, from official rosters: statewide offices, Congress, the
   Legislature, the State Board of Education, the courts (Texas Judicial Branch directory), then
   every county's elected offices in all 254 counties (each county's own website), then cities,
   school districts, and special districts, starting with the 24 counties already covered.
2. **Carry over the campaign.** Every 2026 incumbent and winner already on file gets a person file
   linked to their candidacy, so their records show on the profile at once.
3. **Records for every official,** using the same sources as candidate research: tax-rate notices
   (Tax Code § 26.06 lists each member's vote), meeting minutes and roll calls, legislative journals,
   congressional roll calls, Texas Ethics Commission and local campaign finance reports.
4. **After the canvass,** add the November 2026 winners as incoming holders and close the terms of
   the people they replace.

Collector scripts write seat and person files from saved roster pages and check that every name
appears in the page saved for it. Sites that refuse automated readers are saved by the owner's
browser session first.
