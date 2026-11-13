# Social scout routine

Instructions for the scheduled cloud routine that looks for Instagram post ideas. It runs every hour from
5 a.m. to 10 p.m. Central and opens one pull request per post. It never publishes anything itself: a
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

Then the owner's ideas: notes, shared reels and videos (often screen recordings) the owner sent to
@vote.the.record by DM. The environment has a read-only key for the private bucket they are kept in:

    pip install boto3 -r requirements-listen.txt
    python src/social/inbox.py --fetch /tmp/ideas
    python src/social/listen.py /tmp/ideas/media/<file>     # for each video: transcript.txt + frames

Work on ideas received in the last two hours, and on older ones only if no pull request names their `id`
(`gh pr list --state all --search "<id>"`). Read the note, the transcript and the frames (Read the .jpg files)
to learn the topic. Each idea is a tip, like a news story: find the official record behind it and build the
post on that record only. A machine transcript gets names and numbers wrong; never take a fact from it.

Ideas are private, and this repo and its pull requests are public. Set `found_via: owner idea <id>` (never
the URL), put `Idea: <id>` in the pull request body so later runs skip it, and use `approval: review`. Never
quote the note, the transcript or the video, never describe or reuse the shared reel or recording, and never
upload the files anywhere. Keep everything from an idea under /tmp/ideas: never under
`social/out/` (uploaded as a public run artifact) or anywhere in the repo. In the run summary, list a skipped idea by its `id` and the reason only.

Then search the news for what Congress and state legislatures are doing today. A news story only points
the way: find the official record behind it (a bill page, roll call, public law, journal, calendar) and build
the post on that record. If no official record covers it, skip it.

Check what people are paying attention to, as a signal for topics, timing and formats (never as a source):
- Instagram, through the official API as @vote.the.record:
  https://pub-3587eff321bc4a4d8b4ff4fe8d9a9d0e.r2.dev/state/trends.json (updated daily by
  `.github/workflows/social-trends.yml`: top posts for a few civic hashtags, and recent posts of public
  official accounts)
- Google Trends, United States: https://trends.google.com/trending?geo=US
- TikTok Creative Center, trending hashtags: https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en

A trend only tells you what people are asking about. The post still answers it from an official record, and
never copies or reposts another account's content. Don't log in to, or scrape, Instagram, TikTok or X.

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
The routine runs hourly, so most runs should open nothing. If six or more post pull requests are already
open, open only ones built from the owner's ideas.

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
- `bills`: an entry for every bill the slides or caption name (format in `social/README.md`). Federal: the
  CRS summary from congress.gov (api.congress.gov `/v3/bill/{congress}/{type}/{number}/summaries` with the
  api.data.gov key, if it is set up). Texas: the TLO bill analysis, or the bill text for a newly filed bill.
  `does`, `affects` and `where` never go past the summary; if it doesn't say who is affected, write what it
  does say. A post that names a bill is always `approval: review`, roll-call recaps included.
- `highlight`: on every `story` post and every post with a `story: true` slide, one name from
  `social/highlights.yaml`: the place for state and local posts (e.g. `Harris Co.`), otherwise the topic. If
  the place has no Highlight yet, use `State` or `Local` and say in the pull request that one could be added.
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
