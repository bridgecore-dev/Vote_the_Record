# Social posts

Instagram posts for Vote the Record. Same rules as the site (CLAUDE.md): official sources first, every post
cites its sources, neutral wording, party only as printed on a ballot, never infer past what a source says.

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
approval: auto         # auto: publishes without the owner's tap; review: waits for it
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

The builder refuses candidates out of alphabetical order and any field but `name` and `votes`, so there is
never a party (rule 5). Every bar is the same color and no winner is highlighted; `outcome` is one plain line,
the same for every contest, and only once the canvass is official. Percentages are of all votes shown, write-ins
included. Example:
`examples/list-results-format.yaml` (fictional).

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
  refused: YouTube's terms forbid downloading, and C-SPAN's footage is its own.
- **Cuts:** never mid-sentence. If a question is shown, its answer is too. `context` lets the reviewer check that.
- **No party:** the builder refuses a speaker line with a party marker (rule 5). Captions are verbatim speech and
  are not edited.
- **Same treatment:** the same layout, lower-third style, and caption rules for every member.

**Approval.** `auto` is only for routine items: calendar dates and official tallies from official feeds. The
builder refuses `auto` unless every source URL is on a `.gov`, `.us`, or `.mil` domain. Anything resting on
news, or any judgment call (picking "notable" bills, a summary in our words), uses `review`.

**Record IDs.** `<jurisdiction>/<session or year>/<record>/<event>`, lower-case jurisdiction, the record as
officially written: `tx/90R/HB 101/filed`, `tx/90R/HB 101/house-passed`, `us/119/2/house/roll/316` (roll calls carry the session:
House and Senate numbers restart each session), `us/119/2/senate/roll/257`,
`us/119/public-law/138`, `tx/90R/calendar/convenes`. The event part lets a bill's later actions post again;
the same event never does. A recap post lists every record it covers.

**Words.** Quote official text exactly in `quote`; our own words go in `head`/`text` and must not go beyond
the source. Same wording and structure for every person. Never name or imply a party.

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
