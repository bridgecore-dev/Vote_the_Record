# Social scout routine

Instructions for the scheduled cloud routine that looks for Instagram post ideas. It runs every hour from
5 a.m. to 10 p.m. Central and opens one pull request per post. It never publishes anything itself: a
merged post is published by `src/social/publish.py` (`.github/workflows/social-publish.yml`). Read CLAUDE.md
(especially rules 1-5 and 9) and `social/README.md` before writing anything.

## 1. Set up

    python -m pip install -r requirements.txt   # python -m: the sandbox has more than one Python
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

Recaps: in the first run on a Saturday, draft the week's Congress recap; in the first run on the 1st of a
month, draft the past month's. `python src/social/recap.py --week <any date in Mon-Sun week> --write` (or
`--month YYYY-MM`; add `--reel` for a Reel version when the day has no Reel). It needs `CONGRESS_API_KEY` for
the CRS summaries; without it the draft fails `--check`, so skip the recap and say so in the summary. Fill each
bill's `affects` only from the CRS summary (or write what the summary does say), then continue at step 5's
check. Skip a recap if a pull request for that week or month already exists.

Then the owner's ideas: notes, shared reels and videos (often screen recordings) the owner sent to
@vote.the.record by DM. The environment has a read-only key for the private bucket they are kept in:

    python -m pip install boto3 -r requirements-listen.txt
    python src/social/inbox.py --fetch /tmp/ideas
    python src/social/listen.py /tmp/ideas/media/<file>     # for each video: transcript.txt + frames

Work on ideas received in the last two hours, and on older ones only if no pull request names their `id`
(`gh pr list --state all --search "<id>"`). Read the note, the transcript and the frames (Read the .jpg files)
to learn the topic. Each idea is a tip, like a news story: find the official record behind it and build the
post on that record only. A machine transcript gets names and numbers wrong; never take a fact from it.
A video often covers several topics: list every topic it raises, not just the first, and cover each one.
When covering a topic fairly takes more bills than fit one post (every bill named needs its explainer slide
and caption lines), make a numbered series (`Part 1 of 3`...), one pull request per part, each with the same
selection rule, and say in every part's pull request body that the parts are meant to be merged together.
The three-pull-requests-per-run limit doesn't apply to the parts of one owner idea.

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
- It teaches something, not just announces it. Besides the what and when, the slides say what it means for
  the people who live there, from an official source: what the law or vote changes and for whom, what a
  session or body can and can't do, or what a reader can do (watch, testify, contact, vote). A date or a
  tally alone is not enough; if no official source explains why it matters, skip it. Session calendars and
  schedules only when the break is unusual or long, or a deadline readers can act on is coming.
- Any change is told as before → after (CLAUDE.md "Fair, not silent"): what current law does, what the bill
  or vote changes, who gains, who loses, what it costs and how it's paid for (a CBO estimate, a fiscal note).
  Never a line that only sounds good or bad: "no longer cut" says nothing until the slide says what they get
  now and what they'd get after.
- Every claim on the slides rests on a source you opened in this run, quoted or stated without going past it.
- It's fair: the same wording and layout for every person, party and group, no loaded words, party only as
  the official record lists it. Name the person, party or group whose recorded action it's about, including
  unflattering ones. A judgment ("unconstitutional", "illegal") only when a court or official body said it,
  or in a named speaker's quoted words. When it's unclear whether something helped or hurt, give the
  strongest case for and against, each from a source, and let readers decide.
- If it singles out some items (a "notable" bill, one member's vote), there is a stated, even-handed rule
  for the pick, and it uses `approval: review`.

Vote posts (a roll call, or a bill's passage):
- First slide: the tally with what it means ("392 Yea (for), 18 Nay (against), 23 didn't vote"), the bill's
  short title, and who it affects. On a carousel that names bills, end the first slide with "Swipe to see the
  bills".
- Next slide: the totals by party, exactly as the record gives them (the House Clerk's `totals-by-party` in
  the roll's XML; for the Senate, count each senator's party in the vote's XML).
- Then the names on the smaller side, with party and state as the record lists them ("Roy (R-TX)"):
  if fewer voted Yea than Nay, list every Yea, and the other way around. Up to about 40 names; past that,
  give the totals and link the official list.

Made to be seen (never at the cost of a rule above):
- The first slide is the hook: the number, the question or the claim people are already talking about, in
  as few words as possible. One idea per slide.
- When a claim about a bill or vote is trending (news, Google Trends, the trends file), make an "Is it true?"
  post: state the claim, then what the official record says (the bill text, the roll call, the CBO estimate),
  quoted. If the record shows the claim is right, say so; if it's wrong or about another bill, say that.
- End with what a reader can do, or a question that asks for their view ("Which side convinces you?").
- Reels for anything that fits in 3-8 short slides; carousels for lists and explainers; stories for
  reminders, deadlines and results.

Every day should have at least one Reel (`kind: reel`), one feed post (`carousel` or `image`) and one story
(`kind: story`, or a post with a `story: true` slide). Count the `kind`s already scheduled for each of the
next two days (on `main` and in open `social/*` pull requests) and give a missing format priority. Choose
the format that fits: a Reel for one quick point that takes 3-6 slides, a carousel for something to read
(bill explainers, vote tallies), a story for a reminder or a date. Never weaken the rules above to fill a
format; an empty slot is better than a weak post.

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
  use `approval: review`. Either way the pull request merges itself once its checks pass, so a `review`
  post must be one you'd stand behind if the owner never sees it before it goes out.
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

Then run `python src/social/build.py --check social/posts/<file>.yaml` and fix the file until it passes. Run
only `--check`: don't render slides in this sandbox. The pull request's review workflow builds them and posts
previews; check those before calling anything ready.

## 6. Open the pull request

- Branch `social/<YYYY-MM-DD>-<slug>` from `main`, with only the one post file changed.
- Title: `Post: <the slide's main line>`. Body: the sources, the approval type, and the notes.
- Push and open the pull request against `main`. The review workflow builds it, comments with previews,
  and merges it once its checks pass (any `approval`). The owner stops a post by deleting its file on
  `main` before its slot.

## Never

Edit any other file, merge or approve a pull request, run `publish.py`, touch `data/`, add secrets, say a
party or person did something the record doesn't show, use AI-generated images, or retry a site that blocked
you.

End the run with a short summary: posts opened, ideas skipped and why.
