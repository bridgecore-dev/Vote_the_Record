# Social posts

Instagram posts for Vote the Record. Same rules as the site (CLAUDE.md): official sources first, every post
cites its sources, fair wording ("Fair, not silent"), party only as an official record lists it and the same
for everyone (rule 5), never infer past what a source says.

- `posts/<YYYY-MM-DD>-<slug>.yaml` — one file per post (format below). The file name is the post's ID.
  **Every file here on `main` is published** (src/social/publish.py) once its `publish_after` passes, so a
  post reaches `posts/` only through its own pull request, where merging it is the approval.
- `examples/` — sample posts for trying the builder; never published.
- `out/<post>/` — built files (not committed): `slide-NN.png`, `story.png`, `reel.mp4` + `cover.png`, `manifest.json`.
- `candidates/` — researched facts waiting to be picked.
- `design/` — the original style studies and the profile picture.

Build (preview first with `--check`):

    python src/social/build.py --check social/posts/<post>.yaml
    python src/social/build.py social/posts/<post>.yaml

Needs Chrome (renders slides) and, for Reels, ffmpeg.

## Post file

```yaml
kind: image            # image | carousel (2-10 slides) | story (1-10, 1080x1920) | reel (2-20, 1080x1920 MP4) | clip (below)
level: state           # congress | state | local
place: Texas           # state, or "Harris County, Texas" / "Houston, Texas"; omit for congress
topic: Calendar        # free text; drives highlights later
approval: auto         # auto: routine, from official feeds; review: the owner should check the preview first
layout: card           # optional: card | band | stat; rotates by post name when omitted
story: true            # image only: also render story.png from the first slide
highlight: State       # optional; a name from highlights.yaml: the Story Highlight this story belongs in
sources:               # at least one; each needs title, url, retrieved
- {title: Texas Legislative Council, Dates of Interest, url: https://tlc.texas.gov/..., retrieved: '2026-11-10'}
slides:                # each needs head or big; the rest is optional
- chip: Water                       # small rounded label
  kicker: Mark the calendar         # small caps line
  big: Jan. 12                      # the big number, date, or bill ID
  head: The 90th Texas Legislature convenes at noon on Jan. 12, 2027.
  text: One or two plain sentences.
  quote: {label: Official caption, text: exact words from the source}
  by: "Author: Walle · Filed Nov. 9, 2026"
  rows: [[Budget, Sets the tax rate.], Plain item]   # 1-8 items, text or [label, text]; 4+ become a compact list
  results: {...}                    # a contest's vote counts; see "Lists and results" below
  seconds: 4                        # reel only; default 4
caption: |-            # the post text; sources and hashtags are added below it automatically
  Two months to go. ...
hashtags: [txlege, texas]           # at most 5, without '#'
record_ids: [tx/90R/calendar/convenes]   # what this post covers; the builder refuses an ID another post has
publish_after: '2026-11-14T08:00:00-06:00'  # optional; ISO date-time with a time zone
found_via: https://news.example/story      # optional; what led us here: a news URL, or `owner idea <id>` for an
                                           # idea the owner sent (never its note or link: post files are public).
                                           # Never printed, never a source, and it forces approval: review
rule: How items were picked, when the post is a selection (also say it on a slide).
notes: Anything the reviewer should know; not published.
bills: [...]           # required for every bill the post names; see "Bills" below
```

## Lists and results

**`rows`** holds up to 8 items, each plain text or `[label, text]`. Three or fewer print as paragraphs; four or
more become a compact list, one line each. Use it for a recap (one item per vote or law: `[H.R. 2347, "Passed
the House 251-180 on Nov. 12"]`) or for what an office decides. Keep each item to one line, about 60 characters.

**`results`** shows one contest's vote counts from an official results report:

```yaml
- head: Harris County Judge
  results:
    contest: County Judge            # as the report names it
    status: official                 # unofficial | official, exactly as the report says
    as_of: Nov. 13, 2026             # the report's own date
    reporting: All precincts         # optional, as the report says
    candidates:                      # 1-12; alphabetical by name, never by votes (7+ render compact)
    - {name: Jane Doe, votes: 41250}
    - {name: John Roe, votes: 38710}
    other_votes: 312                 # optional: other write-in votes, counted in the total
    outcome: "Elected: Jane Doe"     # optional; only with status official (the canvass)
    note: Harris County's share of a multi-county contest   # optional small line
    measure: true                    # a ballot measure: keeps the ballot's order (FOR, AGAINST)
```

The builder refuses candidates out of alphabetical order and any field but `name`, `votes` and `party`. A
`party` is only the ballot's listing, and every candidate gets one or none does (`party: null` where the ballot
lists none), so it reads the same for everyone (rule 5). Every bar is the same color and no winner is highlighted; `outcome` is one plain line,
the same for every contest, and only once the canvass is official. Percentages are of all votes shown, write-ins
included. Example:
`examples/list-results-format.yaml` (fictional).

## Story templates

Stories publish through Instagram's API, which can't add interactive stickers (polls, quizzes, links). These
templates work without them; example: `examples/story-templates.yaml`.

- **Countdown:** `countdown: {date: 2027-01-12, event: the 90th Texas Legislature convenes}` draws "57 / days
  until ...". The number is counted from the post's `publish_after` (required), so it's right on the day people
  see it; the date must be that day or later.
- **Quiz:** a slide's `head` is the question and `quiz: {choices: [2-4], answer: 1, explain: ..., quote: ...}`
  (answer counts from 0). The builder shows the choices as A, B, C with "Tap for the answer →" (or "Swipe" on a
  carousel) and adds the answer slide after it, with `explain` and the optional official `quote`. Quizzes are
  about facts and process from the sources, and use `approval: review`.
- **Today:** a `kicker` like "Today in Congress" and a `rows` list, one item per scheduled event.

## Votes

**`vote`** shows a roll call's counts with what each means under it, the same style for every side:

```yaml
- head: The House passed H.R. 2102, the Major Richard Star Act.
  vote: {yea: 392, nay: 18, not_voting: 23}    # also present; exactly as the record gives them
```

Labels: Yea (for), Nay (against), Present, Didn't vote. Totals by party go in `rows`, exactly as the record
gives them, every party in the same form: `[Republican, "181 Yea · 18 Nay · 19 didn't vote"]`.

On a carousel that names bills, the builder ends the first slide with "Swipe to see the bills →",
unless that slide already says "swipe".

## Reels (`kind: reel`)

A Reel is drawn with motion by `src/social/reel.py`: each slide is a scene (a number counts up, rows build in,
a quote types in, bars grow), scenes cut with a short punch-in, and a progress bar runs across the top. Every
Reel is voiced (owner's choice, 2026-11-15; reel.py requires it from that date): each slide and each bill has a
`say`. The other fields are optional, and all of them are for Reels only:

```yaml
voice: true                         # optional: or {name: af_heart, speed: 1.0}; speed 0.8-1.25
slides:
- head: ...
  say: The words read aloud over this slide.     # required on every slide; up to 45 words
  quote: {label: ..., text: ..., key: exact words in text to highlight}
- head: What the bill says
  doc: {url: <official page or PDF>, highlight: exact words on it, page: 3, title: ..., retrieved: 'YYYY-MM-DD'}
- head: ...
  broll: {url: <official video>, start: '0:01:05', end: '0:01:15', title: ..., page: <its official page>, retrieved: ...}
- head: ...
  image: {url: <official photo>, title: ..., license: Public domain ..., subject: building, retrieved: ...}
bills:
- id: H.R. 2102
  say: Read over this bill's explainer slide.   # required on every bill
```

- **The feed alternates** Reel, post, Reel, post each day (owner's choice, 2026-11-15); the time slots are in
  docs/SOCIAL_ROUTINE.md.
- **One or two bills per Reel** (owner's choice, 2026-11-15; reel.py refuses more): a Reel has to be followed
  in one sitting. Split more into another Reel, or use a carousel.
- **`say`** only restates what the slide and its sources already say: every number in it must be on that slide,
  any bill it names needs a `bills` entry, and the party rule applies. Read by Kokoro (run locally, no audio sent
  anywhere) and shown as captions. Every Reel's caption ends with
  "Voice: computer-generated (Kokoro text-to-speech)."
- **`doc`** is a screenshot of an official page or PDF with the exact words highlighted; the build stops if the
  words aren't on the page. Never edited.
- **`broll`** is official video only (the clip rules: `.gov` or an allowed host, never YouTube or C-SPAN),
  2-20 seconds, muted.
- **`image`** is an official public-domain photo of a place, building, or document, or (owner, 2026-11-19) an
  official portrait of a member of Congress: `{subject: member, name: Ro Khanna, bioguide: K000389, url: <our
  storage>, title, license: Public domain, retrieved}`, from the unitedstates/images set by bioguide ID. Every
  member in a post gets the same size and treatment, in alphabetical order by surname (the builder checks the
  order, the ID, and the public-domain license); never AI-generated, never edited.
- Each document, photo, and video page is added to the sources automatically.
- Building needs `pip install -r requirements-reel.txt` and `playwright install chromium` (the GitHub
  workflows install both). Without them, a voiced Reel stops with a clear message; only an older, unvoiced Reel
  can still be built the old way, as still slides.

## Design and trial Reels

`design` (optional) picks the look, for feed slides and Reels alike (the registry is in `src/social/build.py`):

```yaml
design:
  palette: plum        # teal (default) | plum | amber | forest | charcoal; no red or blue (party colors)
  mode: dark           # light (default) | dark: dark background, light ink, one bright accent
  font: round          # serif (default, Source Serif 4) | sans (Public Sans, tighter) | round (Nunito, softer)
  position: bottom     # where text sits: top | center (default) | bottom
  motion: captions     # Reels: one of reel.py's MOTIONS
  sfx: true            # Reels: generated typing, swoosh and pop sounds under the voice
trial: auto            # Reels only: post as an Instagram trial Reel (auto: graduates on performance)
```

Every palette passes contrast checks in both modes (tests/test_social_build.py). The accent goes on the one key
word or number, not on everything. The weekly insights report has a "By design" section comparing these choices.

## AI-generated images

Decoration only (rule 9, owner, 2026-11-19): never a real person, place, building, document, seal, logo or
event, never evidence, and no facts or numbers inside the image. Mark it on the slide (`ai_image: ...`, or
`ai: true` on an `image` or `broll` entry); the builder then requires "AI-generated illustration" in the caption
and `approval: review`.

## Bills: always say what it does

A post that names a bill (H.R. 2347, S. 12, HB 101, SJR 3 …) must say, in plain words, what the bill would do,
who it would affect, and where it stands. A number and a tally alone don't tell anyone anything.

```yaml
bills:
- id: H.R. 2347
  does: Would ... (under 140 characters; what changes, in everyday words)
  affects: Who it would apply to (under 120)
  title: Major Richard Star Act    # optional: the official short title, shown on the explainer
  where: Where it stands now, e.g. "Passed the House Jan. 5; the Senate voted Nov. 10 to take it up." (under 110)
  summary:            # the official summary, quoted exactly (up to 300 characters)
    text: "..."
    title: Congressional Research Service summary of H.R. 2347
    url: https://www.congress.gov/bill/119th-congress/house-bill/2347   # or the legislature's bill analysis
    retrieved: 'YYYY-MM-DD'
```

- The builder puts an explainer slide right after the first slide that names the bill (carousel, story, Reel),
  adds "What it does / Who it affects / Where it stands" to the caption (with 3 or more bills, one line per
  bill, so a recap still fits Instagram's 2,200 characters), and adds the summary to the sources.
- `does`, `affects`, and `where` are our words, so they never go past the official summary or bill text. Where
  the summary doesn't say who is affected, say what it does say ("Applies to taxpayers who ...").
- Federal summaries: the Congressional Research Service summary on congress.gov. Texas: the bill analysis on
  Texas Legislature Online, or the bill text itself for a newly filed bill.
- A single image can't hold this, so a post naming a bill is a carousel, story, or Reel. Explaining a bill in
  our words is a judgment call, so these posts use `approval: review`.
- Example: `examples/bill-format.yaml` (fictional bill).

## Clips (`kind: clip`)

A span of official hearing or floor video, built as a 1080x1920 MP4 (published as a Reel): a title card, the
span inside our frame with the exact words as captions and a lower-third for each speaker, then a source card.
Full example: `examples/clip-format.yaml` (fictional; checks but never builds).

```yaml
kind: clip
approval: review                   # always; the builder refuses auto
video: {url: <official .mp4 or .m3u8>, page: <official hearing page on .gov, also listed in sources>,
        title: Hearing on ..., date: 'YYYY-MM-DD', start: '0:41:05', end: '0:41:47'}   # 5-180 seconds
captions: [{start: 0.0, end: 3.8, text: exact words}]      # seconds from the clip's start; in order, no overlaps
caption_source: official           # official (committee or chamber transcript) or whisper
speakers: [{at: 0.0, name: "Rep. Jane Doe (TX)"}]           # "Sen./Rep. Name (ST)"; witnesses "Name, Title"
context: What was said just before and after; for the reviewer only.
```

- **Sources:** the committee's or chamber's own video. `video.url` must be on `.gov` or a host listed in
  `CLIP_HOSTS` in src/social/clip.py (added only once confirmed as the official feed). YouTube and C-SPAN are
  refused: YouTube's terms forbid downloading, and C-SPAN's footage is its own. Allowed so far: the House floor
  archive behind live.house.gov and the Senate's floor and committee player (senate.gov/isvp). Not yet: Texas
  House video (its terms require written permission), Texas Senate video (no terms stated), and committees that
  post only on YouTube.
- **Rights:** congressional video is public domain (17 U.S.C. 105). House Rule V cl. 2(c) and Rule XI cl. 4(b),
  and S. Res. 28 §6(a), bar using it for partisan political campaign purposes or commercial advertising; our
  posts are neither, and a clip is never cut to make a member look good or bad.
- **Cuts:** never mid-sentence. If a question is shown, its answer is too. `context` lets the reviewer check that.
- **Captions from the official file:** where the chamber publishes captions (the House floor archive's
  `captions.vtt`), `python src/social/clip.py --vtt <url> --start 0:41:05 --end 0:41:47` prints `captions` timed
  from the clip's start, with `caption_source: official`. Check them against the video: live captions can
  repeat words, and are corrected only by removing repeats, never by rewording.
- **Party, as recorded:** a member's lower-third may carry party and state as the Clerk or Senate lists them
  ("Rep. Jane Doe (R-TX)"), and then every member in the clip carries one; the builder refuses a mix, or any other
  form ("Republican", "(R)"). Witnesses have none. Captions are verbatim speech and are not edited.
- **Same treatment:** the same layout, lower-third style, and caption rules for every member.

**Approval.** Every post pull request merges itself once its checks pass (owner's decision, 2026-11-14); the
owner stops a post by deleting its file on `main` before its time. `approval` tells the owner where to look:
`auto` is for routine items (calendar dates, tallies and laws from official feeds), and the builder refuses it
unless every source URL is on a `.gov`, `.us`, or `.mil` domain; anything resting on news, or any judgment call
(picking "notable" bills, a summary in our words), uses `review`, meaning "check this preview first".

**Record IDs.** `<jurisdiction>/<session or year>/<record>/<event>`, lower-case jurisdiction, the record as
officially written: `tx/90R/HB 101/filed`, `tx/90R/HB 101/house-passed`, `us/119/2/house/roll/316` (roll calls carry the session:
House and Senate numbers restart each session), `us/119/2/senate/roll/257`,
`us/119/public-law/138`, `tx/90R/calendar/convenes`. The event part lets a bill's later actions post again;
the same event never does. A recap post lists every record it covers.

**Words.** Quote official text exactly in `quote`; our own words go in `head`/`text` and must not go beyond
the source. Same wording and structure for every person and party. Name who did what on the record, and tell a
change as before → after. Party only as the record lists it (rule 5); never say a party did what the record
doesn't show.

## Owner ideas

The owner can send @vote.the.record a note, a shared reel, or a video they recorded; `src/social/inbox.py`
keeps those in a private bucket and the routine reads them (docs/SOCIAL_ROUTINE.md). They are tips only:
- A post rests on official records, never on the idea. `found_via: owner idea <id>` names it; the builder
  refuses anything else there, so the owner's words and links never reach a public post file or pull request.
- Nothing from an owner's video (frames, audio, transcript) is ever published. Clips take official video only.

## Story Highlights

`highlights.yaml` lists the Highlights (topics, then counties) and each one's icon. `python src/social/covers.py`
draws a cover per Highlight into `out/covers/`: a simple line drawing in the circle Instagram crops to, on the
site's teal; county covers also carry the county's name. Instagram's API can't file a story into a Highlight,
so the owner does that in the app, using the post's `highlight` field (shown in the review comment). To add a
Highlight, add a line to `highlights.yaml`, using one of the icons in `src/social/covers.py`.

## manifest.json

Written next to the files for the publishing step:

```json
{"post": "2026-11-13-texas-session-date", "kind": "image", "level": "state", "place": "Texas",
 "topic": "Calendar", "layout": "stat", "approval": "auto",
 "files": [{"file": "slide-01.png", "alt_text": "..."}, {"file": "story.png", "alt_text": "...", "story": true}],
 "caption": "post text\n\nSource:\nTitle: URL\n\n#tags", "sources": [...],
 "publish_after": "2026-11-14T08:00:00-06:00", "record_ids": ["tx/90R/calendar/convenes"], "found_via": null}
```

`files` is in posting order. A Reel's entry is `{"file": "reel.mp4", "cover": "cover.png", "alt_text": ...}`.
Music, interactive stickers, and GIFs are added in the Instagram app; the Reel has a silent audio track.
