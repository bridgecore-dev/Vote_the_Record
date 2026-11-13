# Social scout routine

Instructions for the scheduled cloud routine that looks for Instagram post ideas. It runs at 5, 8 and 11 a.m.
and 2, 5, 8 and 10 p.m. Central and opens one pull request per post. It never publishes anything itself: a
merged post is published by `src/social/publish.py` (`.github/workflows/social-publish.yml`). Read CLAUDE.md
(especially rules 1-5 and 9) and `social/README.md` before writing anything.

## 1. Set up

    pip install -r requirements.txt
    git fetch origin 'refs/heads/social/*:refs/remotes/origin/social/*'

## 2. Know what is already taken

Collect every `record_ids` entry from `social/posts/*.yaml` on `main` and from the post file on every
`origin/social/*` branch (pull requests not merged yet). Never write a post whose record IDs overlap them.
`build.py --check` also refuses a record ID already in `social/posts/`.

## 3. Look

Official feeds first. These need no key:
- New federal laws: https://www.govinfo.gov/rss/plaw.xml
- House roll calls: https://clerk.house.gov/Votes (newest first)
- Senate roll calls: https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_119_2.htm (use the
  current Congress and session)
- House and Senate floor schedules and committee hearing calendars on house.gov and senate.gov
- State legislatures' official sites (`.gov` or `.us`), e.g. Texas Legislature Online reports at
  https://capitol.texas.gov/Reports/ (bills filed by date, bills passed, calendars)

Then search the news for what Congress and state legislatures are doing today. A news story only points
the way: find the official record behind it (a bill page, roll call, public law, journal, calendar) and build
the post on that record. If no official record covers it, skip it.

congress.gov may refuse automated requests. Don't retry or work around a block; use another official
source or skip.

## 4. Is it post-worthy?

Write a post only when all of these hold:
- It is new since the last run, or a date coming up that people should know.
- It has something concrete: a number, a date, an outcome, a vote tally.
- A 16-year-old would understand it from one sentence.
- Every claim on the slides rests on a source you opened in this run, quoted or stated without going past it.
- It can be told neutrally: no party names, no loaded words, the same wording for everyone involved.
- If it singles out some items (a "notable" bill, one member's vote), there is a stated, even-handed rule
  for the pick, and it uses `approval: review`.

Zero posts is a fine result. At most three pull requests per run; pick the strongest.

Not yet: photos of people. Hearing clips only once `social/README.md` documents `kind: clip`; then follow it
exactly (official video only, never YouTube or C-SPAN; a `context` line; captions from the official transcript
when there is one; always `approval: review`).

## 5. Write the post

One file, `social/posts/<YYYY-MM-DD>-<slug>.yaml`, in the format in `social/README.md`:
- `approval: auto` only for routine items (a scheduled date, or a tally or law straight from an official feed,
  every source on `.gov`, `.us` or `.mil`). If a news story led you there, set `found_via` to its URL and
  use `approval: review`.
- `record_ids`: every record the post covers, in the README's format.
- `publish_after`: the next free slot of 7:30 a.m., 12:00 p.m., 5:30 p.m. and 8:00 p.m. Central not already
  used by a pending post, written with its offset (e.g. `'2026-11-14T12:00:00-06:00'`; `-05:00` in daylight
  time).
- `sources`: title, URL and `retrieved` (today) for each page the post rests on.
- `notes`: one or two lines for the owner: why it's post-worthy and anything to double-check.
- Captions in plain, human words: short sentences, no stock phrases, at most five specific hashtags.

Then run `python src/social/build.py --check social/posts/<file>.yaml` and fix the file until it passes.

## 6. Open the pull request

- Branch `social/<YYYY-MM-DD>-<slug>` from `main`, with only the one post file changed.
- Title: `Post: <the slide's main line>`. Body: the sources, the approval type, and the notes.
- Push and open the pull request against `main`. The review workflow builds it, comments with previews,
  and merges `auto` posts itself.

## Never

Edit any other file, merge or approve a pull request, run `publish.py`, touch `data/`, add secrets, name or
imply a party, use AI-generated images, or retry a site that blocked you.

End the run with a short summary: posts opened, ideas skipped and why.
