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

## 2a. Recheck what posts soon

Before looking for new ideas, read every post on `main` whose `publish_after` falls in the next 24 hours. Redo
any check its `notes` asks for before it posts, against the official source:
- a bill "on the House schedule" or "needs two-thirds": look on clerk.house.gov (floor summary, roll calls) and
  congress.gov actions for a vote since the post was written;
- a bill waiting on the President: check congress.gov for signing or veto;
- any list or status "as of" a date (an IRS list, a court docket): reopen it.

If the record has changed, open a `social/...` pull request that updates that post (the tally, the status, the
"as of" date), cites the new record, and says in `notes` what changed. That fix comes before any new post and
counts toward the run's three pull requests. If nothing changed, leave the post alone. If the check can't be
done (the site is down or blocked), say so in the run's summary; don't guess.

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
- First slide: the tally in the slide's `vote` field (`vote: {yea: 392, nay: 18, not_voting: 23}`, plus
  `present` if any, exactly as the record gives them; the builder labels them "Yea (for)", "Nay (against)"),
  the bill's short title, and who it affects. Don't write "Swipe to see the bills": the builder adds it to
  carousels that name bills.
- Next slide: the totals by party, exactly as the record gives them (the House Clerk's `totals-by-party` in
  the roll's XML; for the Senate, count each senator's party in the vote's XML).
- Then the names on the smaller side, with party and state as the record lists them ("Roy (R-TX)"):
  if fewer voted Yea than Nay, list every Yea, and the other way around. Up to about 40 names; past that,
  give the totals and link the official list.
- Say in plain words what Yea and Nay meant when the question isn't passing the bill itself (owner's
  request, 2026-11-18). A motion to refer, table or recommit often decides the outcome without a vote on the
  bill, and news may call it "blocked". Name the question from the record ("a motion to send it to the
  Judiciary Committee") and give each side: "Yea = send it to committee, so no impeachment vote that day;
  Nay = keep it on the floor, so the House votes on it." Then say what happened as a result.

Made to be seen (never at the cost of a rule above):
- The first slide is the hook: the number, the question or the claim people are already talking about, in
  as few words as possible. One idea per slide. In a Reel, the voice opens a question in the first two
  seconds ("News said the House blocked it. Did it?") that the Reel then answers from the record.
- Double tap (2026-11-18): use the official term, then the same idea in plain words right after it: "a motion
  to refer, a vote to send it to a committee"; "engrossed, the version the House passed". Never the plain words
  alone when the record uses a term people will see in the news.
- Every line of `say` teaches something; cut lines that only set up or repeat a slide. Short sentences, one
  fact each, so the big captions (`motion: captions`) change every second or two.
- Design trials (owner's decision, 2026-11-18): up to one trial Reel a day, `trial: auto` (Instagram shows it
  to non-followers first and adds it to the grid if it does well), with a `design` that changes one thing at
  a time from the last trial (`palette`, `mode`, `font`, `position`, `motion`, `sfx`; see social/README.md).
  The facts and sources follow every rule above; only the look changes. A trial is one of the day's queued
  Reels turned into a trial (owner's choice, 2026-11-19: no extra posts), so it keeps its Reel slot. Use `approval: review` for the first trial of each new design. The weekly "What's
  working" issue compares designs.
- AI illustrations (owner's decision, 2026-11-19): a captions Reel slide may carry `ai_image: {prompt, seed}`, a plain
  description of a decorative scene (landscape, texture, abstract shapes). Cloudflare Workers AI draws it in the post's
  palette with a fixed house style; reel.py refuses prompts that name people, real places, symbols, documents, text,
  numbers or proper names, and shows "AI-generated illustration" on screen. Never on a slide with a vote, quote, doc or
  photo. The caption says "AI-generated illustration" and the post uses `approval: review`.
- When a claim about a bill or vote is trending (news, Google Trends, the trends file), make an "Is it true?"
  post: state the claim, then what the official record says (the bill text, the roll call, the CBO estimate),
  quoted. If the record shows the claim is right, say so; if it's wrong or about another bill, say that.
- End with what a reader can do, or a question that asks for their view ("Which side convinces you?").
- Reels for anything that fits in 3-8 short slides; carousels for lists and explainers; stories for
  reminders, deadlines and results.
- One bill per post (owner's decision, 2026-11-15), so similar bills are never mistaken for each other: when
  several bills are on one subject, each gets its own post, on different slots. The one exception is twins,
  bills with the same text (often a House bill and its Senate companion): they share a post, a slide says
  they are word for word the same, and one bill's `does` starts "Same text as <the other's id>" (the builder
  allows two bills in a Reel only then). Don't name another bill in a post; if a claim is about a different
  bill, say so without its number and give that bill its own post. Weekly recaps and new-law roundups may list
  several bills, one slide each.

Every day should have at least three feed posts (`carousel` or `image`) and three Reels (`kind: reel`), one of
each in the morning, afternoon and evening, plus one story (`kind: story`, or a post with a `story: true` slide);
more on days or weeks with more news (owner's decision, 2026-11-15). Count the `kind`s already scheduled for each
of the next two days (on `main` and in open `social/*` pull requests) and fill the empty slots first, earliest
day first. Choose
the format that fits: a Reel for one quick point that takes 3-6 slides, a carousel for something to read
(bill explainers, vote tallies), a story for a reminder or a date. Never weaken the rules above to fill a
format; an empty slot is better than a weak post.

For the daily story, use a template (examples: `social/examples/story-templates.yaml`):
- Countdown: `countdown: {date, event}` on a slide, with no `big`, for a dated official event (a legislature
  convening, a filing deadline, Election Day), the date and event taken from the official calendar and cited.
  The builder counts the days from `publish_after`, which it requires.
- Quiz: the question in the slide's `head`, then `quiz: {choices, answer, explain, quote}`; the builder adds the
  answer slide. The answer and `explain` come from a cited official record, and a quote is quoted exactly. Quiz
  posts always use `approval: review`. Ask about process and dates, never about a person or a party.
- "Today in Congress": a `kicker` and `rows`, each row one scheduled item from the official House and Senate
  floor schedules or committee calendars.

At most three pull requests per run; pick the strongest. A run with no empty slot in the next two days and
nothing new may open nothing. If twelve or more post pull requests are already open, open only ones built from
the owner's ideas.

Not yet: photos of people.

Hearing and floor clips (`kind: clip`, published as a Reel and filling a Reel slot; owner's request,
2026-11-15). Follow `social/README.md` "Clips" exactly: official video only, a `context` line, cuts never
mid-sentence, a question always with its answer, always `approval: review`. Look for a moment that shows the
record: a member asking about a bill and the answer, a witness stating a fact the post can source, a floor
speech explaining a vote. Choose it for what it shows, never to make someone look good or bad, and give every
member the same treatment. Where the video is:
- Senate committee hearings and the Senate floor: the hearing page embeds
  `https://www.senate.gov/isvp/?comm=<committee>&filename=<file>`; the player loads
  `https://www-senate-gov-media-srs.akamaized.net/hls/live/<stream id>/<committee>/<file>/master.m3u8`
  (floor: `comm=stv`, the file named in senate.gov's floor schedule).
- House floor: live.house.gov; each day's data lists a `manifest.m3u8` and the official `captions.vtt`.
  `python src/social/clip.py --vtt <captions.vtt URL> --start 0:41:05 --end 0:41:47` prints the clip's
  `captions:` and `caption_source: official`, ready to paste. Live House captions are uppercase roll-up text and
  can repeat fragments: watch the frames against them, and fix only by removing repeats, never by rewording.
  If the builder says a House floor host isn't allowed, the host name has probably changed: skip the clip and
  say so in the run summary.
- Not yet: House committee hearings (their video is on YouTube only) and Texas House or Senate video (their
  terms need permission first). Never YouTube or C-SPAN.
If the builder refuses the video's host, skip the clip; never look for a copy elsewhere. At most one clip a day
unless the day's news is a hearing.

## 5. Write the post

One file, `social/posts/<YYYY-MM-DD>-<slug>.yaml`, in the format in `social/README.md`:
- `approval: auto` only for routine items (a scheduled date, or a tally or law straight from an official feed,
  every source on `.gov`, `.us` or `.mil`). If a news story led you there, set `found_via` to its URL and
  use `approval: review`. Either way the pull request merges itself once its checks pass, so a `review`
  post must be one you'd stand behind if the owner never sees it before it goes out.
- `record_ids`: every record the post covers, in the README's format.
- `publish_after`: the next free slot for the post's format, Central time, not already used by a pending post.
  Each day's feed alternates Reel, post, Reel, post (owner's request, 2026-11-15); a `clip` counts as a Reel,
  `carousel` and `image` as posts, and stories sit outside the alternation:
  - around the clock, every 3 hours (owner's decision, 2026-11-19: post as much as Instagram allows without
    slipping, across all 24 hours): Reel 12:00 a.m., post 3:00 a.m., Reel 6:00 a.m., post 9:00 a.m.,
    Reel 12:00 p.m., post 3:00 p.m., Reel 6:00 p.m., post 9:00 p.m.
  - stories: 8:15 p.m.
  - Finding the limit: the publisher logs Meta's rate-limit usage after each post ("Meta rate-limit usage: N%").
    After two days with no limit error, the gap shrinks a step (3 h, then 2.5 h, then 2 h); after a limit error
    that wasn't a post going out anyway, it widens a step. Each change is a pull request that updates these
    slots and re-slots the queue.
  Ordered by time, a day's Reels and posts must never run two of the same kind in a row (a test checks this);
  if the slot that would keep the turn isn't free, take the next day's. Never two posts at the same time.
  Written with its offset (e.g. `'2026-11-16T09:00:00-06:00'`; `-05:00` in daylight time).
- Name every chamber in full (owner's request, 2026-11-15): "U.S. House of Representatives", "U.S. Senate",
  "Texas House of Representatives", "California Senate", never just "the House" or "the Senate". In a `say`,
  write "United States House" (the builder checks that each `say` names its first chamber).
- Every Reel has a voice (owner's request, 2026-11-15): a `say` on every slide and every bill, read by the
  computer voice, and the caption line `Voice: computer-generated (Kokoro text-to-speech).` A `say` restates
  only what its slide shows, in at most 45 words; every number in it must be on the slide (`--check` enforces
  all of this).
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
party or person did something the record doesn't show, use an AI-generated image beyond what CLAUDE.md rule 9 allows (decoration only, labeled, `approval: review`), or retry a site that blocked
you.

End the run with a short summary: posts opened, ideas skipped and why.
